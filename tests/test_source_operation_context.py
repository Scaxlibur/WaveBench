from __future__ import annotations

import pytest

from wavebench.instruments.source_extensions import (
    SourceAffectedClosure,
    SourceEnergyEffect,
    SourceFeature,
    SourceFeatureDirection,
    SourceFieldId,
    SourceFieldRef,
    SourceOperationContract,
    SourceScopeRef,
    SourceStorageEffect,
    SourceV1WriteRouteId,
    SourceFacetScope,
)
from wavebench.services.operation_specs import OperationSpec
from wavebench.services.source_operation_context import (
    SourceBaselineUseState,
    SourceOperationContextCoordinator,
    SourceOperationPhase,
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
        del direction, text

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        del replay
        self.queries.append(command)
        return "ok"

    def query_float_list(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> list[float]:
        del timeout_ms, replay
        self.queries.append(command)
        return [1.0]

    def query_bin_block(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> bytes:
        del replay
        self.queries.append(command)
        return b"data"

    def query_opc(self, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        del replay
        self.queries.append("*OPC?")
        return "1"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def write_bytes(self, command: bytes) -> None:
        self.writes.append(command.decode("ascii"))

    def close(self) -> None:
        pass


def _field(field: SourceFieldId, channel: int | None = None) -> SourceFieldRef:
    target = (
        SourceScopeRef(SourceFacetScope.INSTRUMENT)
        if channel is None
        else SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
    )
    return SourceFieldRef(field, target)


FIELDS = tuple(
    sorted(
        (
            _field(SourceFieldId.BASIC, 1),
            _field(SourceFieldId.OUTPUT, 1),
            _field(SourceFieldId.IDENTITY),
        ),
        key=lambda item: (
            item.field.value,
            item.target.scope.value,
            -1 if item.target.channel is None else item.target.channel,
        ),
    )
)
BASIC = _field(SourceFieldId.BASIC, 1)
OUTPUT = _field(SourceFieldId.OUTPUT, 1)


def _contract(*, energy: SourceEnergyEffect = SourceEnergyEffect.POTENTIAL_WHILE_OFF) -> SourceOperationContract:
    return SourceOperationContract(
        operation="source.basic_configure_v2",
        capability="source.basic_configure_v2",
        feature=SourceFeature.BASIC,
        direction=SourceFeatureDirection.CONFIGURE,
        energy_effect=energy,
        storage_effect=SourceStorageEffect.NONE,
        required_fields=(
            SourceFieldId.BASIC,
            SourceFieldId.OUTPUT,
            SourceFieldId.IDENTITY,
        ),
        changed_fields=(SourceFieldId.BASIC,),
        postcondition_fields=(SourceFieldId.BASIC,),
        cleanup_verification_fields=(SourceFieldId.BASIC, SourceFieldId.OUTPUT),
        v1_equivalent_routes=(SourceV1WriteRouteId.SET_FREQUENCY,),
        v1_overlapping_routes=(),
        operation_timeout_ms=5_000,
        main_max_steps=1,
        recovery_max_steps=2,
        verification_max_steps=2,
    )


def _spec(contract: SourceOperationContract) -> OperationSpec:
    return OperationSpec(
        operation=contract.operation,
        instrument_kind="source",
        required_capabilities=(contract.capability,),
        effect="write",
        lease_mode="exclusive",
        changed_fields=(SourceFieldId.BASIC.value,),
        restore_coverage="source-v2-closure",
        required_verified_fields=(SourceFieldId.IDENTITY.value,),
        verification_fields=(SourceFieldId.BASIC.value, SourceFieldId.OUTPUT.value),
        postcondition_fields=(SourceFieldId.BASIC.value,),
        cleanup_verification_fields=(SourceFieldId.BASIC.value, SourceFieldId.OUTPUT.value),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=contract.operation_timeout_ms,
        risk_flags=("source_v2",),
    )


def _context(
    *,
    energy: SourceEnergyEffect = SourceEnergyEffect.POTENTIAL_WHILE_OFF,
) -> tuple[GuardedAuditedTransport, SourceOperationContextCoordinator]:
    transport = GuardedAuditedTransport(_TextTransport())  # type: ignore[arg-type]
    contract = _contract(energy=energy)
    context = SourceOperationContextCoordinator(
        session_state=transport.session_state,
        operation_spec=_spec(contract),
        operation_contract=contract,
        connection_timeout_ms=1_000,
        baseline_snapshot_digest="sha256:" + "1" * 64,
        fields=FIELDS,
        required_off_outputs=(SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),),
        emergency_off_outputs=(SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),),
        restore_order=(BASIC,),
        non_restorable_fields=(OUTPUT,),
        correlation_id="source-context-test",
    )
    return transport, context


def _preflight(
    transport: GuardedAuditedTransport,
    context: SourceOperationContextCoordinator,
):
    phase = context.make_phase_spec(
        SourceOperationPhase.PREFLIGHT,
        allowed_io={"query"},
        fields=FIELDS,
        max_steps=context.operation_contract.verification_max_steps,
    )
    with context.authorize_phase(phase) as authorization:
        transport.query("SNAPSHOT?")
        context.complete_phase_verification(authorization, io_kind="query", fields=FIELDS)
        baseline = context.create_baseline()
        context.pass_baseline_to_main(baseline)
    return baseline


def _main(
    transport: GuardedAuditedTransport,
    context: SourceOperationContextCoordinator,
) -> None:
    phase = context.make_phase_spec(
        SourceOperationPhase.MAIN,
        allowed_io={"write"},
        fields=(BASIC,),
        max_steps=context.operation_contract.main_max_steps,
    )
    with context.authorize_phase(phase):
        assert not transport.session_state.verified_fields
        transport.write("CONFIGURE")


def test_context_binds_closure_and_successfully_verifies_postcondition() -> None:
    transport, context = _context()
    baseline = _preflight(transport, context)
    _main(transport, context)

    postcondition = context.make_phase_spec(
        SourceOperationPhase.POSTCONDITION,
        allowed_io={"query"},
        fields=(BASIC,),
        max_steps=context.operation_contract.verification_max_steps,
    )
    with context.authorize_phase(postcondition) as authorization:
        transport.query("BASIC?")
        context.complete_phase_verification(authorization, io_kind="query", fields=(BASIC,))
    context.consume_baseline_after_success(baseline)
    context.complete()

    artifact = context.artifact()
    assert context.terminal
    assert transport.session_state.health is SessionHealth.HEALTHY
    assert artifact["closure"]["digest"] == context.closure.closure_digest
    assert artifact["baseline"]["consumption"] == SourceBaselineUseState.CONSUMED.value
    assert baseline.baseline_nonce not in repr(artifact)
    assert [item["phase"] for item in artifact["phases"]] == [
        "preflight",
        "main",
        "postcondition",
    ]


def test_context_can_bind_a_core_snapshot_digest_during_preflight() -> None:
    transport = GuardedAuditedTransport(_TextTransport())  # type: ignore[arg-type]
    contract = _contract()
    context = SourceOperationContextCoordinator(
        session_state=transport.session_state,
        operation_spec=_spec(contract),
        operation_contract=contract,
        connection_timeout_ms=1_000,
        baseline_snapshot_digest=None,
        fields=FIELDS,
        required_off_outputs=(SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),),
        emergency_off_outputs=(SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),),
        restore_order=(BASIC,),
        non_restorable_fields=(OUTPUT,),
        correlation_id="source-context-bind-test",
    )
    placeholder = context.closure.baseline_snapshot_digest
    bound = "sha256:" + "2" * 64
    phase = context.make_phase_spec(
        SourceOperationPhase.PREFLIGHT,
        allowed_io={"query"},
        fields=FIELDS,
        max_steps=2,
    )
    with context.authorize_phase(phase) as authorization:
        with pytest.raises(ValueError, match="not bound"):
            context.create_baseline()
        context.bind_baseline_snapshot_digest(bound)
        baseline = context.create_baseline()
        context.pass_baseline_to_main(baseline)
        transport.query("SNAPSHOT?")
        context.complete_phase_verification(authorization, io_kind="query", fields=FIELDS)

    assert placeholder != bound
    assert context.closure.baseline_snapshot_digest == bound
    context.complete()


def test_failure_path_orders_safe_off_restore_and_cleanup_verification() -> None:
    transport, context = _context()
    baseline = _preflight(transport, context)
    _main(transport, context)
    context.mark_failure_required()

    safe_state = context.make_phase_spec(
        SourceOperationPhase.FAILURE_SAFE_STATE,
        allowed_io={"write"},
        fields=(OUTPUT,),
        max_steps=1,
    )
    with context.authorize_phase(safe_state):
        transport.write("OUTPUT OFF")

    restore = context.make_phase_spec(
        SourceOperationPhase.FAILURE_RESTORE,
        allowed_io={"write"},
        fields=(BASIC,),
        max_steps=context.operation_contract.recovery_max_steps,
    )
    with context.authorize_phase(restore):
        context.begin_restore(baseline)
        transport.write("RESTORE BASIC")
        context.finish_restore(baseline, succeeded=True)

    cleanup = context.make_phase_spec(
        SourceOperationPhase.CLEANUP_VERIFICATION,
        allowed_io={"query"},
        fields=tuple(sorted((BASIC, OUTPUT), key=lambda item: item.field.value)),
        max_steps=context.operation_contract.verification_max_steps,
    )
    with context.authorize_phase(cleanup) as authorization:
        context.begin_cleanup_verification(baseline)
        transport.query("VERIFY?")
        context.finish_cleanup_verification(
            baseline,
            authorization,
            io_kind="query",
            verified_fields=tuple(sorted((BASIC, OUTPUT), key=lambda item: item.field.value)),
            matched=True,
        )
    context.complete()

    assert transport.session_state.health is SessionHealth.HEALTHY
    assert [item["phase"] for item in context.artifact()["phases"]] == [
        "preflight",
        "main",
        "failure_safe_state",
        "failure_restore",
        "cleanup_verification",
    ]


def test_context_rejects_nested_or_out_of_order_phases_before_transport_io() -> None:
    transport, context = _context()
    preflight = context.make_phase_spec(
        SourceOperationPhase.PREFLIGHT,
        allowed_io={"query"},
        fields=FIELDS,
        max_steps=1,
    )
    with context.authorize_phase(preflight):
        nested = context.make_phase_spec(
            SourceOperationPhase.MAIN,
            allowed_io={"write"},
            fields=(BASIC,),
            max_steps=1,
        )
        with pytest.raises(ValueError, match="nested"):
            with context.authorize_phase(nested):
                pass

    main = context.make_phase_spec(
        SourceOperationPhase.MAIN,
        allowed_io={"write"},
        fields=(BASIC,),
        max_steps=1,
    )
    with context.authorize_phase(main):
        transport.write("CONFIGURE")
    context.mark_failure_required()
    restore = context.make_phase_spec(
        SourceOperationPhase.FAILURE_RESTORE,
        allowed_io={"write"},
        fields=(BASIC,),
        max_steps=1,
    )
    with pytest.raises(ValueError, match="safe-state"):
        with context.authorize_phase(restore):
            pass
    assert transport.audit_snapshot()["counters"]["write_completed"] == 1


def test_failure_cleanup_without_matched_verification_poisoned_session() -> None:
    transport, context = _context()
    baseline = _preflight(transport, context)
    _main(transport, context)
    context.mark_failure_required()
    safe_state = context.make_phase_spec(
        SourceOperationPhase.FAILURE_SAFE_STATE,
        allowed_io={"write"},
        fields=(OUTPUT,),
        max_steps=1,
    )
    with context.authorize_phase(safe_state):
        transport.write("OUTPUT OFF")
    restore = context.make_phase_spec(
        SourceOperationPhase.FAILURE_RESTORE,
        allowed_io={"write"},
        fields=(BASIC,),
        max_steps=1,
    )
    with context.authorize_phase(restore):
        context.begin_restore(baseline)
        transport.write("RESTORE BASIC")
        context.finish_restore(baseline, succeeded=False)
    cleanup = context.make_phase_spec(
        SourceOperationPhase.CLEANUP_VERIFICATION,
        allowed_io={"query"},
        fields=tuple(sorted((BASIC, OUTPUT), key=lambda item: item.field.value)),
        max_steps=1,
    )
    with context.authorize_phase(cleanup) as authorization:
        context.begin_cleanup_verification(baseline)
        transport.query("VERIFY?")
        with pytest.raises(ValueError, match="incomplete or mismatched"):
            context.finish_cleanup_verification(
                baseline,
                authorization,
                io_kind="query",
                verified_fields=tuple(sorted((BASIC, OUTPUT), key=lambda item: item.field.value)),
                matched=True,
            )
    context.complete()

    assert transport.session_state.health is SessionHealth.POISONED


def test_unknown_effect_and_reenergizing_restore_are_rejected_without_io() -> None:
    transport = GuardedAuditedTransport(_TextTransport())  # type: ignore[arg-type]
    contract = _contract(energy=SourceEnergyEffect.UNKNOWN)
    with pytest.raises(Exception, match="unknown Source energy effect"):
        SourceOperationContextCoordinator(
            session_state=transport.session_state,
            operation_spec=_spec(contract),
            operation_contract=contract,
            connection_timeout_ms=1_000,
            baseline_snapshot_digest="sha256:" + "1" * 64,
            fields=FIELDS,
            required_off_outputs=(SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),),
            emergency_off_outputs=(SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),),
            restore_order=(BASIC,),
            non_restorable_fields=(OUTPUT,),
        )
    with pytest.raises(ValueError, match="cannot re-enable"):
        SourceOperationContextCoordinator(
            session_state=transport.session_state,
            operation_spec=_spec(_contract()),
            operation_contract=_contract(),
            connection_timeout_ms=1_000,
            baseline_snapshot_digest="sha256:" + "1" * 64,
            fields=FIELDS,
            required_off_outputs=(SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),),
            emergency_off_outputs=(SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),),
            restore_order=(OUTPUT,),
            non_restorable_fields=(BASIC,),
        )
    assert transport.audit_snapshot()["counters"]["write_completed"] == 0
    assert transport.audit_snapshot()["counters"]["query_calls"] == 0


def test_affected_closure_digest_rejects_tampering() -> None:
    transport, context = _context()
    closure = context.closure
    assert closure.context_id == context.context_id
    with pytest.raises(ValueError, match="digest"):
        SourceAffectedClosure(
            operation=closure.operation,
            context_id=closure.context_id,
            session_epoch=closure.session_epoch,
            baseline_snapshot_digest=closure.baseline_snapshot_digest,
            fields=closure.fields,
            required_off_outputs=closure.required_off_outputs,
            emergency_off_outputs=closure.emergency_off_outputs,
            restore_order=closure.restore_order,
            non_restorable_fields=closure.non_restorable_fields,
            closure_digest="sha256:" + "0" * 64,
        )
