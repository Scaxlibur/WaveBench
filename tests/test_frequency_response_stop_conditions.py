from types import SimpleNamespace

from wavebench.services.run_service import RunService


def _point(status: str, gain_db: float | None = 0.0):
    return SimpleNamespace(status=status, gain_db=gain_db, amplitude_index=0)


def test_point_failures_remain_tolerant_without_explicit_stop_condition() -> None:
    assert RunService._frequency_response_stop_reason([_point("failed")], None) is None


def test_explicit_failure_budget_stops_a_group() -> None:
    reason = RunService._frequency_response_stop_reason(
        [_point("ok"), _point("failed")], {"max_failed_points": 1}
    )
    assert reason is not None
    assert "max_failed_points" in reason


def test_gain_jump_condition_is_auditable() -> None:
    reason = RunService._frequency_response_stop_reason(
        [_point("ok", 0.0), _point("ok", 8.0)], {"max_gain_jump_db": 3.0}
    )
    assert reason is not None
    assert "max_gain_jump_db" in reason
