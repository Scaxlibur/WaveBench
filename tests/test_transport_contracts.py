from __future__ import annotations

import pytest

from wavebench.errors import TransportIOError, error_envelope
from wavebench.transport.contracts import (
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)


def test_replay_policy_values_are_stable() -> None:
    assert [policy.value for policy in ReplayPolicy] == [
        "safe_to_replay",
        "no_replay",
        "read_continuation_only",
    ]


def test_transport_error_serializes_stable_fields_without_payload() -> None:
    cause = TimeoutError("backend timed out")
    try:
        raise TransportIOError(
            "query failed after a partial response",
            operation="query_binary",
            phase=TransportPhase.READING,
            replay_policy=ReplayPolicy.NO_REPLAY,
            command_transmission=CommandTransmission.SENT,
            response_progress=ResponseProgress.PARTIAL,
            synchronization=Synchronization.LOST,
            attempts=1,
        ) from cause
    except TransportIOError as exc:
        payload = error_envelope(exc, operation="scope.fetch_waveform")

    assert payload["code"] == "transport_io_error"
    assert payload["operation"] == "scope.fetch_waveform"
    assert payload["details"] == {
        "transport_operation": "query_binary",
        "phase": "reading",
        "replay_policy": "no_replay",
        "command_transmission": "sent",
        "response_progress": "partial",
        "synchronization": "lost",
        "attempts": 1,
    }
    assert payload["cause"]["type"] == "TimeoutError"
    assert "command" not in payload["details"]
    assert "response" not in payload["details"]


def test_before_send_failure_requires_zero_transmissions() -> None:
    with pytest.raises(ValueError, match="before_send"):
        TransportIOError(
            "invalid continuation request",
            operation="query",
            phase=TransportPhase.BEFORE_SEND,
            replay_policy=ReplayPolicy.READ_CONTINUATION_ONLY,
            command_transmission=CommandTransmission.UNKNOWN,
            response_progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
        )
