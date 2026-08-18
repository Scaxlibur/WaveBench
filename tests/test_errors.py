from __future__ import annotations

import json

from wavebench.errors import (
    ERROR_SCHEMA,
    ConfigError,
    ErrorEnvelope,
    InstrumentError,
    SessionHealthError,
    error_envelope,
    ensure_error_envelope,
)


def test_wavebench_error_keeps_numeric_exit_code_and_adds_stable_code() -> None:
    error = ConfigError("bad plan")

    payload = error.to_envelope(operation="run.check", details={"step": 2}).as_dict()

    assert payload == {
        "schema": ERROR_SCHEMA,
        "code": "config_error",
        "type": "ConfigError",
        "message": "bad plan",
        "exit_code": 2,
        "operation": "run.check",
        "details": {"step": 2},
    }


def test_error_envelope_serializes_nested_cause_without_traceback() -> None:
    payload = error_envelope(
        InstrumentError("write failed"),
        operation="source.output",
        cause=ConfigError("source is missing"),
    )

    assert payload["code"] == "instrument_error"
    assert payload["cause"]["code"] == "config_error"
    assert "traceback" not in payload["cause"]


def test_keyboard_interrupt_has_shell_compatible_exit_code() -> None:
    payload = error_envelope(KeyboardInterrupt())

    assert payload["code"] == "interrupted"
    assert payload["type"] == "KeyboardInterrupt"
    assert payload["exit_code"] == 130


def test_error_envelope_is_json_compatible() -> None:
    payload = ErrorEnvelope(
        code="custom",
        message="message",
        exit_code=7,
        error_type="CustomError",
    ).as_dict()
    assert payload["schema"] == ERROR_SCHEMA


def test_session_health_error_is_zero_io_and_does_not_serialize_reason_or_cause() -> None:
    error = SessionHealthError(
        "blocked: SECRET:VALUE",
        health="poisoned",
        io_kind="query",
        epoch_id="epoch-1",
    )
    payload = error.to_envelope(
        operation="scope.fetch",
        cause=RuntimeError("backend payload SECRET"),
    ).as_dict()

    assert payload["code"] == "session_health_error"
    assert "SECRET" not in payload["message"]
    assert payload["details"] == {
        "session_health": "poisoned",
        "io_kind": "query",
        "command_transmission": "not_sent",
        "response_progress": "none",
        "synchronization": "proven",
        "attempts": 0,
        "epoch_id": "epoch-1",
    }
    assert "cause" not in payload
    assert json.loads(json.dumps(payload)) == payload


def test_legacy_error_mapping_is_augmented_without_dropping_custom_fields() -> None:
    payload = ensure_error_envelope(
        {"type": "StepFailure", "message": "failed", "step_index": 3},
        default_code="step_failed",
        default_exit_code=2,
    )

    assert payload["schema"] == ERROR_SCHEMA
    assert payload["code"] == "step_failed"
    assert payload["exit_code"] == 2
    assert payload["step_index"] == 3
