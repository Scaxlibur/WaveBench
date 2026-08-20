from __future__ import annotations

import time

import pytest

from wavebench.errors import TransportIOError
from wavebench.services.scope_extension_specs import EXPERIMENTAL_SCOPE_OPERATION_SPECS
from wavebench.services.scope_phase_coordinator import (
    OperationPhase,
    ScopeOperationContextCoordinator,
)
from wavebench.logging import CommandLogger
from wavebench.transport.binary import BinaryQueryLedger, parse_definite_block_response
from wavebench.transport.contracts import (
    BinaryQueryResult,
    BinaryResponseFraming,
    ReplayPolicy,
    Synchronization,
)
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.pyvisa_transport import PyVisaTransport
from wavebench.transport.rsinstrument_transport import RsInstrumentTransport
from wavebench.transport.serial_transport import SerialTransport
from wavebench.transport.session import SessionHealth


def test_definite_block_parser_preserves_real_header_and_exact_trailing() -> None:
    result = parse_definite_block_response(
        b"#3004data\n",
        max_bytes=8,
        transport_trailing=b"\n",
    )

    assert result.data == b"data"
    assert result.declared_length == 4
    assert result.framing_header_bytes == 5
    assert result.consumed_bytes == 10
    assert result.transport_trailing_bytes == b"\n"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"#0payload", "binary_framing_error"),
        (b"#2x1", "binary_framing_error"),
        (b"#24", "binary_truncated"),
        (b"#14dataextra", "binary_transport_trailing_error"),
    ],
)
def test_definite_block_parser_rejects_malformed_or_unbounded_responses(
    raw: bytes,
    reason: str,
) -> None:
    with pytest.raises(TransportIOError) as raised:
        parse_definite_block_response(raw, max_bytes=32)

    assert raised.value.reason_code == reason
    assert raised.value.consumed_bytes is not None


def test_definite_block_limit_error_preserves_structured_byte_evidence() -> None:
    with pytest.raises(TransportIOError) as raised:
        parse_definite_block_response(b"#14data", max_bytes=3)

    error = raised.value
    assert error.reason_code == "binary_limit_exceeded"
    assert error.synchronization is Synchronization.PROVEN
    assert error.consumed_bytes == 7
    assert error.discarded_bytes == 4
    copied = error.with_attempts(2)
    assert copied.reason_code == error.reason_code
    assert copied.consumed_bytes == error.consumed_bytes
    assert copied.to_envelope().details["discarded_bytes"] == 4


def test_binary_result_enforces_message_and_definite_accounting() -> None:
    message = BinaryQueryResult(
        data=b"png",
        framing=BinaryResponseFraming.MESSAGE,
        declared_length=None,
        framing_header_bytes=0,
        consumed_bytes=3,
    )
    assert message.synchronization is Synchronization.PROVEN

    with pytest.raises(ValueError, match="consumed"):
        BinaryQueryResult(
            data=b"x",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            declared_length=1,
            framing_header_bytes=3,
            consumed_bytes=99,
        )


def _ledger(*, query_count: int = 2, total: int = 8) -> BinaryQueryLedger:
    return BinaryQueryLedger(
        context_id="ctx",
        operation_id="scope.fetch_trace",
        correlation_id="corr",
        session_epoch="epoch",
        deadline=time.monotonic() + 10,
        per_response_max_bytes=4,
        operation_max_bytes=total,
        query_max_count=query_count,
        resynchronization_max_bytes=2,
        transport_trailing=b"",
    )


def test_binary_ledger_binds_context_and_never_refunds_failed_queries() -> None:
    ledger = _ledger(query_count=1, total=4)
    budget = ledger.issue_budget()
    with pytest.raises(ValueError, match="per-response"):
        ledger.reserve(
            budget,
            context_id="ctx",
            operation_id="scope.fetch_trace",
            correlation_id="corr",
            session_epoch="epoch",
            max_bytes=5,
        )
    with pytest.raises(ValueError, match="binding"):
        ledger.reserve(
            budget,
            context_id="other",
            operation_id="scope.fetch_trace",
            correlation_id="corr",
            session_epoch="epoch",
            max_bytes=4,
        )

    reservation = ledger.reserve(
        budget,
        context_id="ctx",
        operation_id="scope.fetch_trace",
        correlation_id="corr",
        session_epoch="epoch",
        max_bytes=4,
    )
    ledger.fail(reservation, synchronization_proven=True)
    assert ledger.snapshot()["remaining_query_count"] == 0
    with pytest.raises(ValueError, match="count budget"):
        ledger.reserve(
            budget,
            context_id="ctx",
            operation_id="scope.fetch_trace",
            correlation_id="corr",
            session_epoch="epoch",
            max_bytes=1,
        )


def test_binary_ledger_is_cumulative_and_invalidates_on_lost_sync() -> None:
    ledger = _ledger(query_count=2, total=6)
    budget = ledger.issue_budget()
    first = ledger.reserve(
        budget,
        context_id="ctx",
        operation_id="scope.fetch_trace",
        correlation_id="corr",
        session_epoch="epoch",
        max_bytes=4,
    )
    ledger.commit(
        first,
        BinaryQueryResult(
            data=b"1234",
            framing=BinaryResponseFraming.MESSAGE,
            declared_length=None,
            framing_header_bytes=0,
            consumed_bytes=4,
        ),
    )
    second = ledger.reserve(
        budget,
        context_id="ctx",
        operation_id="scope.fetch_trace",
        correlation_id="corr",
        session_epoch="epoch",
        max_bytes=4,
    )
    assert second.effective_max_bytes == 2
    ledger.fail(second, discarded_bytes=1, synchronization_proven=False)
    assert ledger.snapshot()["active"] is False


class _BinaryBackend:
    resource = "fake"

    def __init__(self, *, wrong_result: bool = False) -> None:
        self.calls = 0
        self.closed = 0
        self.wrong_result = wrong_result
        self.timeout_ms: int | None = None

    def record_event(self, direction: str, text: str) -> None:
        pass

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        return "ok"

    def query_binary(
        self,
        command: str,
        *,
        framing: BinaryResponseFraming,
        max_bytes: int,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> BinaryQueryResult:
        self.calls += 1
        self.timeout_ms = timeout_ms
        result_framing = (
            BinaryResponseFraming.MESSAGE if self.wrong_result else framing
        )
        if result_framing is BinaryResponseFraming.MESSAGE:
            return BinaryQueryResult(
                data=b"data",
                framing=result_framing,
                declared_length=None,
                framing_header_bytes=0,
                consumed_bytes=4,
            )
        return BinaryQueryResult(
            data=b"data",
            framing=result_framing,
            declared_length=4,
            framing_header_bytes=3,
            consumed_bytes=7,
        )

    def close(self) -> None:
        self.closed += 1


def _binary_context(transport: GuardedAuditedTransport) -> ScopeOperationContextCoordinator:
    context = ScopeOperationContextCoordinator(
        session_state=transport.session_state,
        spec=EXPERIMENTAL_SCOPE_OPERATION_SPECS["scope.fetch_trace"],
        connection_timeout_ms=1_000,
        enabled=True,
    )
    preflight = context.make_phase_spec(
        OperationPhase.PREFLIGHT,
        allowed_io={"query"},
        fields={"scope.identity"},
        max_steps=1,
    )
    with context.authorize_phase(preflight) as authorization:
        transport.query("*IDN?")
        context.complete_phase_verification(
            authorization,
            io_kind="query",
            fields={"scope.identity"},
        )
    return context


def test_guarded_binary_query_requires_context_budget_and_debits_it() -> None:
    backend = _BinaryBackend()
    guarded = GuardedAuditedTransport(backend)  # type: ignore[arg-type]
    with pytest.raises(TransportIOError) as raised:
        guarded.query_binary(
            "DATA?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=8,
        )
    assert raised.value.reason_code == "binary_budget_missing"
    assert backend.calls == 0

    context = _binary_context(guarded)
    phase = context.make_phase_spec(
        OperationPhase.MAIN,
        allowed_io={"query_binary"},
        fields={"scope.waveform_transfer_window"},
        max_steps=1,
    )
    with context.authorize_phase(phase):
        result = guarded.query_binary(
            "DATA?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=8,
        )
    assert result.data == b"data"
    assert context.binary_ledger is not None
    assert context.binary_ledger.snapshot()["remaining_query_count"] == 255
    assert backend.timeout_ms is not None
    assert 1 <= backend.timeout_ms <= 1_000
    context.complete()


def test_guarded_binary_contract_violation_poison_closes_backend() -> None:
    backend = _BinaryBackend(wrong_result=True)
    guarded = GuardedAuditedTransport(backend)  # type: ignore[arg-type]
    context = _binary_context(guarded)
    phase = context.make_phase_spec(
        OperationPhase.MAIN,
        allowed_io={"query_binary"},
        fields={"scope.waveform_transfer_window"},
        max_steps=1,
    )
    with context.authorize_phase(phase):
        with pytest.raises(TransportIOError, match="violated"):
            guarded.query_binary(
                "DATA?",
                framing=BinaryResponseFraming.DEFINITE_BLOCK,
                max_bytes=8,
            )
    assert guarded.session_state.health is SessionHealth.POISONED
    assert backend.closed == 1


@pytest.mark.parametrize(
    "transport",
    [
        PyVisaTransport("fake", object(), object(), CommandLogger()),
        RsInstrumentTransport("fake", object(), CommandLogger()),
        SerialTransport("fake", object(), CommandLogger()),
    ],
)
def test_existing_backends_reject_new_binary_contract_before_send(transport) -> None:
    with pytest.raises(TransportIOError) as raised:
        transport.query_binary(
            "DATA?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=16,
        )
    assert raised.value.reason_code == "binary_framing_unsupported"
    assert raised.value.attempts == 0
