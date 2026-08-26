from __future__ import annotations

from contextlib import nullcontext
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
        _transport_trailing: bytes = b"",
        _resynchronization_max_bytes: int = 0,
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
            transport_trailing_bytes=_transport_trailing,
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


@pytest.mark.parametrize(
    ("replay", "reason_code"),
    (
        (ReplayPolicy.SAFE_TO_REPLAY, "binary_replay_unsupported"),
        (ReplayPolicy.READ_CONTINUATION_ONLY, "binary_continuation_unsupported"),
    ),
)
def test_guarded_bounded_binary_rejects_replay_before_backend_send(
    replay: ReplayPolicy,
    reason_code: str,
) -> None:
    backend = _BinaryBackend()
    guarded = GuardedAuditedTransport(backend)  # type: ignore[arg-type]
    context = _binary_context(guarded)
    phase = context.make_phase_spec(
        OperationPhase.MAIN,
        allowed_io={"query_binary"},
        fields={"scope.waveform_transfer_window"},
        max_steps=1,
    )

    with context.authorize_phase(phase):
        with pytest.raises(TransportIOError) as raised:
            guarded.query_binary(
                "DATA?",
                framing=BinaryResponseFraming.DEFINITE_BLOCK,
                max_bytes=4,
                replay=replay,
            )

    assert raised.value.reason_code == reason_code
    assert raised.value.attempts == 0
    assert backend.calls == 0


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


class _FakeVisaLib:
    def __init__(self, owner: "_FakeVisaSession") -> None:
        self.owner = owner

    def read(self, handle: object, count: int):
        from pyvisa.constants import StatusCode

        assert handle is self.owner.session
        if self.owner.read_error is not None:
            raise self.owner.read_error
        chunk = self.owner.response[:count]
        self.owner.response = self.owner.response[len(chunk) :]
        status = (
            StatusCode.success_max_count_read
            if len(chunk) == count
            else StatusCode.success
        )
        return chunk, status


class _FakeVisaSession:
    resource_class = "INSTR"
    wavebench_message_boundary: bool | None = None

    def __init__(
        self,
        response: bytes,
        *,
        resource_name: str = "TCPIP::example::INSTR",
    ) -> None:
        self.response = response
        self.resource_name = resource_name
        self.timeout = 12_345
        self.read_termination = "\n"
        self.session = object()
        self.visalib = _FakeVisaLib(self)
        self.commands: list[str] = []
        self.read_error: BaseException | None = None

    def write(self, command: str) -> int:
        self.commands.append(command)
        return len(command)

    def query(self, command: str) -> str:
        self.commands.append(command)
        return "EXAMPLE,SCOPE,1,1"

    def ignore_warning(self, *statuses):
        return nullcontext()


class _RestoreFailureVisaSession(_FakeVisaSession):
    def __setattr__(self, name: str, value: object) -> None:
        if (
            name == "read_termination"
            and value == "\n"
            and getattr(self, "fail_termination_restore", False)
        ):
            raise RuntimeError("restore failed")
        super().__setattr__(name, value)


class _FakeRsSession:
    def __init__(self, raw: _FakeVisaSession) -> None:
        self.raw = raw
        self.write_str_calls = 0

    def get_session_handle(self) -> _FakeVisaSession:
        return self.raw

    def write_str(self, command: str) -> None:
        self.write_str_calls += 1
        self.raw.commands.append(command)


@pytest.mark.parametrize("backend", ["pyvisa", "rsinstrument"])
def test_real_backends_stream_bounded_definite_blocks_and_restore_settings(backend: str) -> None:
    raw = _FakeVisaSession(b"#14data\n")
    rs_session: _FakeRsSession | None = None
    if backend == "pyvisa":
        transport = PyVisaTransport("fake", object(), raw, CommandLogger())
    else:
        rs_session = _FakeRsSession(raw)
        transport = RsInstrumentTransport("fake", rs_session, CommandLogger())

    result = transport.query_binary(
        "DATA?",
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
        max_bytes=4,
        timeout_ms=250,
        _transport_trailing=b"\n",
    )

    assert result.data == b"data"
    assert result.framing_header_bytes == 3
    assert result.consumed_bytes == 8
    assert result.transport_trailing_bytes == b"\n"
    assert raw.commands == ["DATA?"]
    assert raw.response == b""
    assert raw.timeout == 12_345
    assert raw.read_termination == "\n"
    if rs_session is not None:
        assert rs_session.write_str_calls == 0


@pytest.mark.parametrize("backend", ["pyvisa", "rsinstrument"])
@pytest.mark.parametrize("replay", [ReplayPolicy.NO_REPLAY, ReplayPolicy.SAFE_TO_REPLAY])
def test_visa_binary_query_is_always_one_send(backend: str, replay: ReplayPolicy) -> None:
    raw = _FakeVisaSession(b"#14data")
    if backend == "pyvisa":
        transport = PyVisaTransport("fake", object(), raw, CommandLogger())
    else:
        transport = RsInstrumentTransport("fake", _FakeRsSession(raw), CommandLogger())

    result = transport.query_binary(
        "DATA?",
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
        max_bytes=4,
        replay=replay,
    )

    assert result.data == b"data"
    assert raw.commands == ["DATA?"]


@pytest.mark.parametrize("backend", ["pyvisa", "rsinstrument"])
def test_visa_message_framing_requires_and_uses_proven_eom(backend: str) -> None:
    raw = _FakeVisaSession(b"png")
    if backend == "pyvisa":
        transport = PyVisaTransport("fake", object(), raw, CommandLogger())
    else:
        transport = RsInstrumentTransport("fake", _FakeRsSession(raw), CommandLogger())

    result = transport.query_binary(
        "DISPLAY?",
        framing=BinaryResponseFraming.MESSAGE,
        max_bytes=8,
        timeout_ms=250,
    )

    assert result.data == b"png"
    assert result.framing is BinaryResponseFraming.MESSAGE
    assert result.consumed_bytes == 3
    assert raw.commands == ["DISPLAY?"]


def test_visa_message_eom_at_exact_payload_limit_uses_bounded_probe() -> None:
    raw = _FakeVisaSession(b"data")
    transport = PyVisaTransport("fake", object(), raw, CommandLogger())

    result = transport.query_binary(
        "DISPLAY?",
        framing=BinaryResponseFraming.MESSAGE,
        max_bytes=4,
    )

    assert result.data == b"data"
    assert result.consumed_bytes == 4
    assert raw.response == b""


def test_guarded_context_drives_real_pyvisa_backend_with_opaque_budget() -> None:
    raw = _FakeVisaSession(b"#14data")
    backend = PyVisaTransport("fake", object(), raw, CommandLogger())
    guarded = GuardedAuditedTransport(backend)
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
            max_bytes=4,
        )

    assert result.data == b"data"
    assert raw.commands == ["*IDN?", "DATA?"]
    assert context.binary_ledger is not None
    assert context.binary_ledger.snapshot()["remaining_query_count"] == 255
    context.complete()


@pytest.mark.parametrize(
    "framing",
    [BinaryResponseFraming.DEFINITE_BLOCK, BinaryResponseFraming.MESSAGE],
)
def test_pyvisa_binary_framing_rejects_socket_resource_before_send(framing) -> None:
    raw = _FakeVisaSession(b"png", resource_name="TCPIP::example::5025::SOCKET")
    transport = PyVisaTransport("fake", object(), raw, CommandLogger())

    with pytest.raises(TransportIOError) as raised:
        transport.query_binary(
            "DISPLAY?",
            framing=framing,
            max_bytes=8,
        )

    assert raised.value.reason_code == "binary_framing_unsupported"
    assert raised.value.attempts == 0
    assert raw.commands == []


def test_definite_block_over_limit_uses_only_authorized_resynchronization() -> None:
    raw = _FakeVisaSession(b"#15abcde")
    transport = PyVisaTransport("fake", object(), raw, CommandLogger())

    with pytest.raises(TransportIOError) as raised:
        transport.query_binary(
            "DATA?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=4,
            _resynchronization_max_bytes=5,
        )

    assert raised.value.reason_code == "binary_limit_exceeded"
    assert raised.value.synchronization is Synchronization.PROVEN
    assert raised.value.discarded_bytes == 5
    assert raw.response == b""
    assert raw.timeout == 12_345
    assert raw.read_termination == "\n"


def test_definite_block_over_limit_without_resync_stops_at_header() -> None:
    raw = _FakeVisaSession(b"#15abcde")
    transport = PyVisaTransport("fake", object(), raw, CommandLogger())

    with pytest.raises(TransportIOError) as raised:
        transport.query_binary(
            "DATA?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=4,
        )

    assert raised.value.reason_code == "binary_limit_exceeded"
    assert raised.value.synchronization is Synchronization.LOST
    assert raised.value.consumed_bytes == 3
    assert raw.response == b"abcde"


def test_definite_block_resync_counts_profiled_transport_trailing() -> None:
    raw = _FakeVisaSession(b"#15abcde\n")
    transport = PyVisaTransport("fake", object(), raw, CommandLogger())

    with pytest.raises(TransportIOError) as raised:
        transport.query_binary(
            "DATA?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=4,
            _transport_trailing=b"\n",
            _resynchronization_max_bytes=6,
        )

    assert raised.value.reason_code == "binary_limit_exceeded"
    assert raised.value.synchronization is Synchronization.PROVEN
    assert raised.value.discarded_bytes == 6
    assert raw.response == b""


def test_definite_block_rejects_unprofiled_trailing_bytes() -> None:
    raw = _FakeVisaSession(b"#14dataextra")
    transport = PyVisaTransport("fake", object(), raw, CommandLogger())

    with pytest.raises(TransportIOError) as raised:
        transport.query_binary(
            "DATA?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=4,
        )

    assert raised.value.reason_code == "binary_transport_trailing_error"
    assert raised.value.synchronization is Synchronization.LOST
    assert raised.value.discarded_bytes == 1
    assert raw.response == b"xtra"


def test_binary_timeout_is_structured_and_restores_visa_settings() -> None:
    from pyvisa.constants import StatusCode
    from pyvisa.errors import VisaIOError

    raw = _FakeVisaSession(b"")
    raw.read_error = VisaIOError(StatusCode.error_timeout)
    transport = PyVisaTransport("fake", object(), raw, CommandLogger())

    with pytest.raises(TransportIOError) as raised:
        transport.query_binary(
            "DATA?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=4,
            timeout_ms=250,
        )

    assert raised.value.reason_code == "binary_timeout"
    assert raised.value.synchronization is Synchronization.UNPROVEN
    assert raw.timeout == 12_345
    assert raw.read_termination == "\n"


def test_termination_restore_failure_is_lost_synchronization() -> None:
    raw = _RestoreFailureVisaSession(b"#14data")
    raw.fail_termination_restore = True
    transport = PyVisaTransport("fake", object(), raw, CommandLogger())

    with pytest.raises(TransportIOError) as raised:
        transport.query_binary(
            "DATA?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=4,
            timeout_ms=250,
        )

    assert raised.value.reason_code == "binary_transport_trailing_error"
    assert raised.value.synchronization is Synchronization.LOST
    assert raised.value.response_progress.value == "complete"


def test_guarded_transport_poison_closes_on_termination_restore_failure() -> None:
    raw = _RestoreFailureVisaSession(b"#14data")
    backend = PyVisaTransport("fake", object(), raw, CommandLogger())
    guarded = GuardedAuditedTransport(backend)
    context = _binary_context(guarded)
    raw.fail_termination_restore = True
    phase = context.make_phase_spec(
        OperationPhase.MAIN,
        allowed_io={"query_binary"},
        fields={"scope.waveform_transfer_window"},
        max_steps=1,
    )

    with context.authorize_phase(phase):
        with pytest.raises(TransportIOError):
            guarded.query_binary(
                "DATA?",
                framing=BinaryResponseFraming.DEFINITE_BLOCK,
                max_bytes=4,
            )

    assert guarded.session_state.health is SessionHealth.POISONED
    assert guarded._closed is True
    context.complete()
