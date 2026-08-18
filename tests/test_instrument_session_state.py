from __future__ import annotations

import pytest

from wavebench.transport.session import InstrumentSessionState, SessionHealth


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

    state._complete_verification(
        {"identity", "scope.timebase"},
        reason="bounded_recovery_verified",
    )

    assert state.health is SessionHealth.HEALTHY
    assert state.verified_fields == {"identity", "scope.timebase"}


def test_close_is_idempotent_and_terminal() -> None:
    state = InstrumentSessionState()
    state.close()
    state.close()

    assert state.health is SessionHealth.CLOSED
    with pytest.raises(ValueError, match="cannot verify"):
        state._complete_verification({"identity"}, reason="too_late")
