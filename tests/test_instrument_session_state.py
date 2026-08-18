from __future__ import annotations

import pytest

from wavebench.transport.session import (
    InstrumentSessionState,
    SessionHealth,
    SessionPurpose,
    SessionTransactionCoordinator,
)


def test_new_sessions_have_unique_epochs_and_no_verified_fields() -> None:
    first = InstrumentSessionState()
    second = InstrumentSessionState()

    assert first.epoch_id != second.epoch_id
    assert first.snapshot()["health"] == "healthy"
    assert first.snapshot()["verified_fields"] == []


def test_degrade_is_monotonic_and_clears_verified_fields() -> None:
    state = InstrumentSessionState(verified_fields={"identity", "scope.timebase"})

    state.degrade(SessionHealth.UNCERTAIN, reason="write_result_unknown")
    assert state.health is SessionHealth.UNCERTAIN
    assert state.verified_fields == set()

    state.degrade(SessionHealth.POISONED, reason="synchronization_lost")
    with pytest.raises(ValueError, match="cannot improve"):
        state.degrade(SessionHealth.HEALTHY, reason="invalid")


def test_only_complete_verification_can_restore_uncertain_to_healthy() -> None:
    state = InstrumentSessionState()
    state.degrade(SessionHealth.UNCERTAIN, reason="write_result_unknown")

    with pytest.raises(ValueError, match="coordinator-owned"):
        state._complete_verification(
            {"identity", "scope.timebase"},
            reason="bounded_recovery_verified",
        )

    coordinator = SessionTransactionCoordinator(state)
    with coordinator.authorize(
        operation_id="verify-state",
        purpose=SessionPurpose.VERIFICATION,
        allowed_io={"query"},
        fields={"identity", "scope.timebase"},
        timeout_ms=1000,
        max_steps=1,
        evidence_fields={"query": {"identity", "scope.timebase"}},
    ) as authorization:
        state._record_authorized_success(authorization, "query")
        coordinator.record_evidence(
            authorization,
            "query",
            {"identity", "scope.timebase"},
        )
        coordinator.complete_verification(authorization)

    assert state.health is SessionHealth.HEALTHY
    assert state.verified_fields == {"identity", "scope.timebase"}


def test_close_is_idempotent_and_terminal() -> None:
    state = InstrumentSessionState()
    state.close()
    state.close()

    assert state.health is SessionHealth.CLOSED
    with pytest.raises(ValueError, match="cannot verify"):
        state._complete_verification(
            {"identity"},
            reason="too_late",
            _issuer=state._authorization_nonce,
        )


def test_authorization_rejects_string_fields_and_nested_ranges() -> None:
    state = InstrumentSessionState()
    coordinator = SessionTransactionCoordinator(state)
    with pytest.raises(ValueError, match="iterable of field names"):
        with coordinator.authorize(
            operation_id="bad-fields",
            purpose=SessionPurpose.RECOVERY,
            allowed_io={"query"},
            fields="scope.timebase",  # type: ignore[arg-type]
            timeout_ms=1000,
            max_steps=1,
        ):
            pass

    with coordinator.authorize(
        operation_id="outer-auth",
        purpose=SessionPurpose.RECOVERY,
        allowed_io={"query"},
        fields={"scope.timebase"},
        timeout_ms=1000,
        max_steps=1,
    ):
        with pytest.raises(ValueError, match="nested"):
            with coordinator.authorize(
                operation_id="inner-auth",
                purpose=SessionPurpose.RECOVERY,
                allowed_io={"query"},
                fields={"scope.timebase"},
                timeout_ms=1000,
                max_steps=1,
            ):
                pass


def test_verification_authorization_expires_before_completion() -> None:
    state = InstrumentSessionState()
    coordinator = SessionTransactionCoordinator(state)
    with coordinator.authorize(
        operation_id="expired-auth",
        purpose=SessionPurpose.VERIFICATION,
        allowed_io={"query"},
        fields={"identity"},
        timeout_ms=1,
        max_steps=1,
        evidence_fields={"query": {"identity"}},
    ) as authorization:
        state._record_authorized_success(authorization, "query")
        coordinator.record_evidence(authorization, "query", {"identity"})
        import time

        time.sleep(0.01)
        with pytest.raises(ValueError, match="expired"):
            coordinator.complete_verification(authorization)
