from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


ERROR_SCHEMA = "wavebench.error.v1"


@dataclass(frozen=True)
class ErrorEnvelope:
    """Stable, JSON-compatible representation of one failed operation."""

    code: str
    message: str
    exit_code: int
    error_type: str
    operation: str | None = None
    details: Mapping[str, Any] | None = None
    cause: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": ERROR_SCHEMA,
            "code": self.code,
            "type": self.error_type,
            "message": self.message,
            "exit_code": self.exit_code,
        }
        if self.operation is not None:
            payload["operation"] = self.operation
        if self.details:
            payload["details"] = dict(self.details)
        if self.cause is not None:
            payload["cause"] = dict(self.cause)
        return payload


class WaveBenchError(Exception):
    """Base class for expected WaveBench failures."""

    exit_code = 1
    code = "wavebench_error"

    def to_envelope(
        self,
        *,
        operation: str | None = None,
        details: Mapping[str, Any] | None = None,
        cause: Mapping[str, Any] | BaseException | None = None,
    ) -> ErrorEnvelope:
        return ErrorEnvelope(
            code=self.code,
            message=str(self),
            exit_code=self.exit_code,
            error_type=type(self).__name__,
            operation=operation,
            details=details,
            cause=_cause_payload(cause),
        )

class ConfigError(WaveBenchError):
    exit_code = 2
    code = "config_error"


class ExecutionIntentError(ConfigError):
    code = "execution_intent_mismatch"

    def __init__(
        self,
        message: str,
        *,
        expected_digest: str | None = None,
        actual_digest: str | None = None,
        expected_plan_digest: str | None = None,
        actual_plan_digest: str | None = None,
        expected_config_digest: str | None = None,
        actual_config_digest: str | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_digest = expected_digest
        self.actual_digest = actual_digest
        self.expected_plan_digest = expected_plan_digest
        self.actual_plan_digest = actual_plan_digest
        self.expected_config_digest = expected_config_digest
        self.actual_config_digest = actual_config_digest

    def to_envelope(
        self,
        *,
        operation: str | None = None,
        details: Mapping[str, Any] | None = None,
        cause: Mapping[str, Any] | BaseException | None = None,
    ) -> ErrorEnvelope:
        merged = dict(details or {})
        merged.setdefault("expected_digest", self.expected_digest)
        merged.setdefault("actual_digest", self.actual_digest)
        merged.setdefault("expected_plan_digest", self.expected_plan_digest)
        merged.setdefault("actual_plan_digest", self.actual_plan_digest)
        merged.setdefault("expected_config_digest", self.expected_config_digest)
        merged.setdefault("actual_config_digest", self.actual_config_digest)
        return super().to_envelope(operation=operation, details=merged, cause=cause)


class AccessDeniedError(ConfigError):
    exit_code = 2
    code = "access_denied"

class ConnectionError(WaveBenchError):
    exit_code = 3
    code = "connection_error"

class InstrumentError(WaveBenchError):
    exit_code = 4
    code = "instrument_error"


class StateDriftError(InstrumentError):
    code = "state_drift"

    def __init__(
        self,
        message: str,
        *,
        expected: Mapping[str, Any] | None = None,
        actual: Mapping[str, Any] | None = None,
        diff: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.expected = dict(expected or {})
        self.actual = dict(actual or {})
        self.diff = dict(diff or {})

    def to_envelope(
        self,
        *,
        operation: str | None = None,
        details: Mapping[str, Any] | None = None,
        cause: Mapping[str, Any] | BaseException | None = None,
    ) -> ErrorEnvelope:
        merged = dict(details or {})
        merged.setdefault("expected", self.expected)
        merged.setdefault("actual", self.actual)
        merged.setdefault("diff", self.diff)
        return super().to_envelope(operation=operation, details=merged, cause=cause)


class OperationTimeout(WaveBenchError):
    exit_code = 5
    code = "operation_timeout"

class DataError(WaveBenchError):
    exit_code = 6
    code = "data_error"


class ResourceBusyError(WaveBenchError):
    exit_code = 7
    code = "resource_busy"


def error_envelope(
    exc: BaseException,
    *,
    operation: str | None = None,
    details: Mapping[str, Any] | None = None,
    cause: Mapping[str, Any] | BaseException | None = None,
) -> dict[str, Any]:
    """Build a versioned error payload without serializing tracebacks."""

    if isinstance(exc, WaveBenchError):
        return exc.to_envelope(
            operation=operation,
            details=details,
            cause=cause,
        ).as_dict()
    if isinstance(exc, KeyboardInterrupt):
        envelope = ErrorEnvelope(
            code="interrupted",
            message=str(exc) or "run interrupted by user",
            exit_code=130,
            error_type=type(exc).__name__,
            operation=operation,
            details=details,
            cause=_cause_payload(cause),
        )
        return envelope.as_dict()
    envelope = ErrorEnvelope(
        code="unexpected_error",
        message=str(exc) or type(exc).__name__,
        exit_code=1,
        error_type=type(exc).__name__,
        operation=operation,
        details=details,
        cause=_cause_payload(cause),
    )
    return envelope.as_dict()


def ensure_error_envelope(
    payload: Mapping[str, Any],
    *,
    default_code: str = "run_failed",
    default_exit_code: int = 1,
) -> dict[str, Any]:
    """Add the envelope fields to a legacy error mapping without dropping data."""

    normalized = dict(payload)
    normalized.setdefault("schema", ERROR_SCHEMA)
    normalized.setdefault("code", default_code)
    normalized.setdefault("type", "WaveBenchError")
    normalized.setdefault("message", "WaveBench operation failed")
    normalized.setdefault("exit_code", default_exit_code)
    return normalized


def _cause_payload(cause: Mapping[str, Any] | BaseException | None) -> dict[str, Any] | None:
    if cause is None:
        return None
    if isinstance(cause, Mapping):
        return dict(cause)
    return error_envelope(cause)
