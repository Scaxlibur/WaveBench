from __future__ import annotations

import pytest

from wavebench.services.scope_extension_specs import EXPERIMENTAL_SCOPE_OPERATION_SPECS
from wavebench.services.scope_phase_coordinator import (
    BaselineUseState,
    OperationPhase,
    ScopeBinaryLimits,
    ScopeOperationContextCoordinator,
)
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import SessionHealth


class _TextTransport:
    resource = "fake"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.writes: list[str] = []

    def record_event(self, direction: str, text: str) -> None:
        pass

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
        return [1.0]

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

    def write(self, command: str) -> None:
        self.writes.append(command)

    def write_bytes(self, command: bytes) -> None:
        pass

    def close(self) -> None:
        pass


FIELDS = (
    "scope.query_response_header",
    "scope.waveform_byte_order",
    "scope.waveform_transfer_window",
)


def _context() -> tuple[
    GuardedAuditedTransport,
    ScopeOperationContextCoordinator,
]:
    transport = GuardedAuditedTransport(_TextTransport())  # type: ignore[arg-type]
    context = ScopeOperationContextCoordinator(
        session_state=transport.session_state,
        spec=EXPERIMENTAL_SCOPE_OPERATION_SPECS["scope.fetch_trace"],
        connection_timeout_ms=1_000,
        enabled=True,
    )
    return transport, context


def _baseline(
    transport: GuardedAuditedTransport,
    context: ScopeOperationContextCoordinator,
):
    phase = context.make_phase_spec(
        OperationPhase.PREFLIGHT,
        allowed_io={"query"},
        fields=FIELDS,
        max_steps=1,
    )
    with context.authorize_phase(phase):
        transport.query("SNAPSHOT?")
        handle = context.create_baseline(
            kind="trace_transfer",
            fields=FIELDS,
            restore_order=FIELDS,
        )
        context.pass_baseline_to_main(handle)
    return handle


def _main(
    transport: GuardedAuditedTransport,
    context: ScopeOperationContextCoordinator,
) -> None:
    phase = context.make_phase_spec(
        OperationPhase.MAIN,
        allowed_io={"query"},
        fields=FIELDS,
        max_steps=1,
    )
    with context.authorize_phase(phase):
        transport.query("FETCH?")
    context.mark_cleanup_required()


def test_restore_and_verify_slots_are_distinct_and_single_use() -> None:
    transport, context = _context()
    handle = _baseline(transport, context)
    nonce = handle.baseline_nonce
    _main(transport, context)

    restore = context.make_phase_spec(
        OperationPhase.SUCCESS_RESTORE,
        allowed_io={"write"},
        fields=FIELDS,
        max_steps=1,
    )
    with context.authorize_phase(restore):
        context.begin_restore(handle)
        with pytest.raises(ValueError, match="restore slot"):
            context.begin_restore(handle)
        transport.write("RESTORE")
        context.finish_restore(handle, succeeded=True)

    verify = context.make_phase_spec(
        OperationPhase.CLEANUP_VERIFICATION,
        allowed_io={"query"},
        fields=FIELDS,
        max_steps=1,
    )
    with context.authorize_phase(verify) as authorization:
        context.begin_verification(handle)
        transport.query("VERIFY?")
        context.finish_verification(
            handle,
            authorization,
            io_kind="query",
            verified_fields=FIELDS,
            matched=True,
        )
        with pytest.raises(ValueError, match="prior restore"):
            context.begin_verification(handle)

    context.complete()
    artifact = context.artifact()
    assert transport.session_state.health is SessionHealth.HEALTHY
    assert artifact["baselines"][0]["consumption"] == BaselineUseState.CONSUMED.value
    assert nonce not in repr(artifact)
    assert artifact["baselines"][0]["nonce_digest"] != nonce


def test_restore_failure_allows_one_diagnostic_verify_but_never_recovers_health() -> None:
    transport, context = _context()
    handle = _baseline(transport, context)
    _main(transport, context)

    restore = context.make_phase_spec(
        OperationPhase.FAILURE_CLEANUP,
        allowed_io={"write"},
        fields=FIELDS,
        max_steps=1,
    )
    with context.authorize_phase(restore):
        context.begin_restore(handle)
        transport.write("PARTIAL_RESTORE")
        context.finish_restore(handle, succeeded=False)

    verify = context.make_phase_spec(
        OperationPhase.CLEANUP_VERIFICATION,
        allowed_io={"query"},
        fields=FIELDS,
        max_steps=1,
    )
    with context.authorize_phase(verify) as authorization:
        context.begin_verification(handle)
        transport.query("DIAGNOSTIC_VERIFY?")
        with pytest.raises(ValueError, match="incomplete or mismatched"):
            context.finish_verification(
                handle,
                authorization,
                io_kind="query",
                verified_fields=FIELDS,
                matched=True,
            )

    assert transport.session_state.health is SessionHealth.UNCERTAIN
    context.complete()
    assert transport.session_state.health is SessionHealth.POISONED
    assert context.artifact()["baselines"][0]["consumption"] == "consumed"


def test_cross_context_baseline_and_nested_phase_are_rejected_before_io() -> None:
    transport, first = _context()
    handle = _baseline(transport, first)
    second = ScopeOperationContextCoordinator(
        session_state=transport.session_state,
        spec=EXPERIMENTAL_SCOPE_OPERATION_SPECS["scope.fetch_trace"],
        connection_timeout_ms=1_000,
        enabled=True,
    )

    phase = first.make_phase_spec(
        OperationPhase.MAIN,
        allowed_io={"query"},
        fields=FIELDS,
        max_steps=1,
    )
    with first.authorize_phase(phase) as authorization:
        assert authorization.deadline == phase.deadline
        assert authorization._session_authorization.io_timeout_ms == 1_000
        nested = first.make_phase_spec(
            OperationPhase.ERROR_AFTER,
            allowed_io={"query"},
            fields={"scope.error_queue"},
            max_steps=1,
        )
        with pytest.raises(ValueError, match="nested"):
            with first.authorize_phase(nested):
                pass

    with pytest.raises(ValueError, match="not owned"):
        second._baseline_record(handle)


def test_error_before_failure_does_not_require_cleanup_when_main_never_enters() -> None:
    transport, context = _context()
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
    before = context.make_phase_spec(
        OperationPhase.ERROR_BEFORE,
        allowed_io={"query"},
        fields={"scope.error_queue"},
        max_steps=1,
    )
    with context.authorize_phase(before):
        transport.query("ERROR?")
    context.complete()
    assert transport.session_state.health is SessionHealth.HEALTHY


def test_context_reserves_cleanup_time_inside_hard_operation_deadline() -> None:
    _, context = _context()
    assert context.main_deadline < context.deadline
    assert 1_000 <= context.cleanup_reserve_ms <= 5_000


def test_context_intersects_spec_profile_and_connection_binary_limits() -> None:
    transport = GuardedAuditedTransport(_TextTransport())  # type: ignore[arg-type]
    context = ScopeOperationContextCoordinator(
        session_state=transport.session_state,
        spec=EXPERIMENTAL_SCOPE_OPERATION_SPECS["scope.fetch_trace"],
        connection_timeout_ms=1_000,
        profile_binary_limits=ScopeBinaryLimits(
            response_max_bytes=4_096,
            operation_max_bytes=16_384,
            query_max_count=8,
            resynchronization_max_bytes=512,
        ),
        connection_binary_limits=ScopeBinaryLimits(
            response_max_bytes=2_048,
            operation_max_bytes=8_192,
            query_max_count=4,
            resynchronization_max_bytes=256,
        ),
        enabled=True,
    )

    assert context.binary_ledger is not None
    assert context.binary_ledger.snapshot() == {
        "ledger_id": context.binary_ledger.ledger_id,
        "active": True,
        "per_response_max_bytes": 2_048,
        "operation_max_bytes": 8_192,
        "remaining_operation_bytes": 8_192,
        "query_max_count": 4,
        "remaining_query_count": 4,
        "resynchronization_max_bytes": 256,
        "discarded_bytes": 0,
        "transport_trailing_bytes": 0,
        "required_framing": None,
    }


def test_main_cannot_skip_preflight() -> None:
    _, context = _context()
    main = context.make_phase_spec(
        OperationPhase.MAIN,
        allowed_io={"query"},
        fields={"scope.identity"},
        max_steps=1,
    )
    with pytest.raises(ValueError, match="require preflight"):
        with context.authorize_phase(main):
            pass


def test_default_feature_gate_is_closed() -> None:
    transport = GuardedAuditedTransport(_TextTransport())  # type: ignore[arg-type]
    with pytest.raises(Exception, match="disabled"):
        ScopeOperationContextCoordinator(
            session_state=transport.session_state,
            spec=EXPERIMENTAL_SCOPE_OPERATION_SPECS["scope.fetch_trace"],
            connection_timeout_ms=1_000,
        )
