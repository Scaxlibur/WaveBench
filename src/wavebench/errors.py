from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from wavebench.transport.contracts import (
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)


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


class TransportIOError(InstrumentError):
    """Structured transport failure without command or response payloads."""

    code = "transport_io_error"

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        phase: TransportPhase,
        replay_policy: ReplayPolicy,
        command_transmission: CommandTransmission,
        response_progress: ResponseProgress,
        synchronization: Synchronization,
        attempts: int,
    ) -> None:
        super().__init__(message)
        phase = TransportPhase(phase)
        replay_policy = ReplayPolicy(replay_policy)
        command_transmission = CommandTransmission(command_transmission)
        response_progress = ResponseProgress(response_progress)
        synchronization = Synchronization(synchronization)
        if attempts < 0:
            raise ValueError("transport attempts must be >= 0")
        if phase is TransportPhase.BEFORE_SEND and (
            command_transmission is not CommandTransmission.NOT_SENT
            or response_progress is not ResponseProgress.NONE
            or synchronization is not Synchronization.PROVEN
            or attempts != 0
        ):
            raise ValueError("before_send failures must prove zero command transmissions")
        if command_transmission is CommandTransmission.NOT_SENT and attempts != 0:
            raise ValueError("not_sent failures must have zero command transmissions")
        if command_transmission is CommandTransmission.NOT_SENT and (
            phase is not TransportPhase.BEFORE_SEND
            or response_progress is not ResponseProgress.NONE
            or synchronization is not Synchronization.PROVEN
        ):
            raise ValueError("not_sent failures must be proven before_send failures")
        if phase is not TransportPhase.BEFORE_SEND and attempts == 0:
            raise ValueError("post-send failures must report at least one attempt")
        if response_progress in {ResponseProgress.PARTIAL, ResponseProgress.COMPLETE} and attempts == 0:
            raise ValueError("response progress requires at least one command transmission")
        self.operation = operation
        self.phase = phase
        self.replay_policy = replay_policy
        self.command_transmission = command_transmission
        self.response_progress = response_progress
        self.synchronization = synchronization
        self.attempts = attempts

    def with_attempts(self, attempts: int) -> "TransportIOError":
        return TransportIOError(
            str(self),
            operation=self.operation,
            phase=self.phase,
            replay_policy=self.replay_policy,
            command_transmission=self.command_transmission,
            response_progress=self.response_progress,
            synchronization=self.synchronization,
            attempts=attempts,
        )

    def to_envelope(
        self,
        *,
        operation: str | None = None,
        details: Mapping[str, Any] | None = None,
        cause: Mapping[str, Any] | BaseException | None = None,
    ) -> ErrorEnvelope:
        merged = dict(details or {})
        merged.update(
            {
                "transport_operation": self.operation,
                "phase": self.phase.value,
                "replay_policy": self.replay_policy.value,
                "command_transmission": self.command_transmission.value,
                "response_progress": self.response_progress.value,
                "synchronization": self.synchronization.value,
                "attempts": self.attempts,
            }
        )
        return super().to_envelope(
            operation=operation,
            details=merged,
            cause=(
                _sanitized_transport_cause(self.__cause__)
                if cause is None
                else _sanitized_transport_cause(cause)
            ),
        )


class SessionHealthError(InstrumentError):
    """An instrument exchange was rejected by the shared session gate."""

    code = "session_health_error"

    def __init__(
        self,
        message: str,
        *,
        health: str,
        io_kind: str,
        epoch_id: str,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", health):
            raise ValueError("invalid session health code")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", io_kind):
            raise ValueError("invalid session I/O kind")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", epoch_id):
            raise ValueError("invalid session epoch id")
        # Ignore caller text: this error may cross an untrusted transport
        # boundary, so its stable message must never echo command/response data.
        super().__init__(
            f"transport {io_kind} is blocked because session health is {health!r}"
        )
        self.health = health
        self.io_kind = io_kind
        self.epoch_id = epoch_id

    def to_envelope(
        self,
        *,
        operation: str | None = None,
        details: Mapping[str, Any] | None = None,
        cause: Mapping[str, Any] | BaseException | None = None,
    ) -> ErrorEnvelope:
        merged = dict(details or {})
        merged.update(
            {
                "session_health": self.health,
                "io_kind": self.io_kind,
                "command_transmission": "not_sent",
                "response_progress": "none",
                "synchronization": "proven",
                "attempts": 0,
                "epoch_id": self.epoch_id,
            }
        )
        return super().to_envelope(
            operation=operation,
            details=merged,
            cause=None,
        )


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


def _sanitized_transport_cause(
    cause: Mapping[str, Any] | BaseException | None,
) -> dict[str, Any] | None:
    if cause is None:
        return None
    if isinstance(cause, Mapping):
        payload = {"type": str(cause.get("type", "BackendError"))}
        if "code" in cause:
            payload["code"] = str(cause["code"])
        return payload
    return {"type": type(cause).__name__}
