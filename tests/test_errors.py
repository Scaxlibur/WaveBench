from __future__ import annotations

from wavebench.errors import (
    ERROR_SCHEMA,
    ConfigError,
    ErrorEnvelope,
    InstrumentError,
    error_envelope,
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
