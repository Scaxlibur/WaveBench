"""Private core-owned error policy executor for the Draft scope R1.3 RFC."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Iterable

from wavebench.errors import ConfigError, InstrumentError
from wavebench.instruments.scope_extensions import (
    DriverErrorRecord,
    ErrorCheckSpec,
    ErrorDrainResult,
    ErrorRecord,
)

from .operation_specs import OperationSpec
from .scope_phase_coordinator import (
    OperationPhase,
    ScopeOperationContextCoordinator,
)


_POLICY_STRENGTH = {"disabled": 0, "if_supported": 1, "required": 2}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PRIVATE_PATTERN = re.compile(
    r"(?:\b(?:\d{1,3}\.){3}\d{1,3}\b|TCPIP\S*|USB\S*|COM\d+|[A-Za-z]:\\\S+|/\S+)",
    re.IGNORECASE,
)


def resolve_error_check(
    operation_spec: OperationSpec,
    override: ErrorCheckSpec | None,
    *,
    instrument_default: ErrorCheckSpec | None = None,
) -> ErrorCheckSpec | None:
    """Resolve one operation policy without allowing a weaker override."""

    minimum = operation_spec.error_check_minimum
    if minimum is None:
        if override is not None:
            raise ConfigError(
                f"operation {operation_spec.operation!r} does not accept error-check overrides"
            )
        return None
    selected = override or instrument_default or ErrorCheckSpec(policy=minimum)
    if _POLICY_STRENGTH[selected.policy] < _POLICY_STRENGTH[minimum]:
        raise ConfigError(
            f"operation {operation_spec.operation!r} requires error policy {minimum!r} or stronger"
        )
    if operation_spec.effect in {"write", "acquire"} and (
        selected.on_instrument_error != "fail"
    ):
        raise ConfigError("write/acquire operations cannot record instrument errors and continue")
    return selected


def _scrub_record(
    record: DriverErrorRecord,
    *,
    correlation_id: str,
) -> ErrorRecord:
    original = record.message
    cleaned = _CONTROL.sub(" ", original)
    cleaned = _PRIVATE_PATTERN.sub("[redacted]", cleaned)
    cleaned = " ".join(cleaned.split())
    redacted = cleaned != original
    if not cleaned or len(cleaned) > 512 or not cleaned.isprintable():
        cleaned = "instrument reported an error"
        redacted = True
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return ErrorRecord(
        code=record.code,
        message=cleaned,
        message_redacted=redacted,
        severity=record.severity,
        source=record.source,
        observed_at_utc=observed,
        correlation_id=correlation_id,
    )


@dataclass(slots=True)
class ScopeErrorPolicyExecutor:
    driver: object
    capabilities: frozenset[str]
    operation_spec: OperationSpec
    error_spec: ErrorCheckSpec | None
    correlation_id: str
    artifact: dict[str, object] = field(init=False)

    def __post_init__(self) -> None:
        supported = "scope.error_drain_v1" in self.capabilities
        if self.error_spec is None:
            self.artifact = {
                "executor": None,
                "status": "disabled",
                "reason_code": "not_applicable",
                "checks": [],
                "attempted_phases": [],
                "completed_phases": [],
                "omitted_phases": [],
                "last_drain_terminated": None,
                "main_operation_sent": False,
            }
            return
        policy = self.error_spec.policy
        if policy == "disabled":
            self.artifact = self._base_artifact(supported=None)
            self.artifact.update(status="disabled", reason_code="not_applicable")
            return
        if not supported:
            if policy == "required":
                raise ConfigError("scope.error_drain_v1 is required but unsupported")
            self.artifact = self._base_artifact(supported=False)
            self.artifact.update(status="skipped", reason_code="unsupported")
            return
        if not callable(getattr(self.driver, "drain_errors", None)):
            raise ConfigError("scope.error_drain_v1 requires callable drain_errors()")
        self.artifact = self._base_artifact(supported=True)
        self.artifact.update(status="completed", reason_code="empty")

    def _base_artifact(self, *, supported: bool | None) -> dict[str, object]:
        assert self.error_spec is not None
        return {
            "executor": "core_v1",
            "policy": self.error_spec.policy,
            "capability": "scope.error_drain_v1",
            "supported": supported,
            "status": "completed",
            "reason_code": "empty",
            "timing": self.error_spec.timing,
            "max_records": self.error_spec.max_records,
            "on_instrument_error": self.error_spec.on_instrument_error,
            "checks": [],
            "attempted_phases": [],
            "completed_phases": [],
            "omitted_phases": [],
            "last_drain_terminated": None,
            "main_operation_sent": False,
            "diagnostic_evidence_id": None,
        }

    @property
    def enabled(self) -> bool:
        return bool(
            self.error_spec is not None
            and self.error_spec.policy != "disabled"
            and self.artifact.get("supported") is True
        )

    def wants(self, phase: str) -> bool:
        if not self.enabled or self.error_spec is None:
            return False
        return self.error_spec.timing in {phase, "before_and_after"}

    def mark_main_sent(self) -> None:
        self.artifact["main_operation_sent"] = True

    def omit_after(self, reason_code: str) -> None:
        if not self.wants("after"):
            return
        if reason_code not in {"main_operation_failed", "session_unhealthy", "cancelled"}:
            raise ValueError("unsupported omitted error phase reason")
        omitted = self.artifact["omitted_phases"]
        assert isinstance(omitted, list)
        omitted.append({"phase": "after", "reason_code": reason_code})

    def run(
        self,
        context: ScopeOperationContextCoordinator,
        *,
        phase: str,
    ) -> tuple[ErrorRecord, ...]:
        if phase not in {"before", "after"}:
            raise ValueError("error phase must be before or after")
        if not self.wants(phase):
            return ()
        assert self.error_spec is not None
        operation_phase = (
            OperationPhase.ERROR_BEFORE if phase == "before" else OperationPhase.ERROR_AFTER
        )
        phase_spec = context.make_phase_spec(
            operation_phase,
            allowed_io={"query"},
            fields={"scope.error_queue"},
            max_steps=self.error_spec.max_records + 1,
        )
        attempted = self.artifact["attempted_phases"]
        assert isinstance(attempted, list)
        attempted.append(phase)
        try:
            with context.authorize_phase(phase_spec) as authorization:
                result = self.driver.drain_errors(
                    max_records=self.error_spec.max_records
                )
                if not isinstance(result, ErrorDrainResult):
                    raise TypeError("drain_errors() returned an invalid result")
                result.validate_for(max_records=self.error_spec.max_records)
                actual_queries = authorization._session_authorization._record.successful_io.get(
                    "query", 0
                )
                if actual_queries != result.query_count:
                    raise ValueError("error drain query_count does not match guarded transport evidence")
        except Exception:
            self.artifact.update(status="failed", reason_code="query_failed")
            self.artifact["last_drain_terminated"] = None
            raise

        records = tuple(
            _scrub_record(item, correlation_id=self.correlation_id)
            for item in result.records
        )
        overflow = (
            _scrub_record(result.overflow_record, correlation_id=self.correlation_id)
            if result.overflow_record is not None
            else None
        )
        check = {
            "phase": phase,
            "status": "completed" if result.terminated else "failed",
            "reason_code": (
                "empty"
                if result.terminated and not records
                else "records"
                if result.terminated
                else "error_queue_incomplete"
            ),
            "query_count": result.query_count,
            "terminated": result.terminated,
            "records": list(records),
            "overflow_record": overflow,
        }
        checks = self.artifact["checks"]
        assert isinstance(checks, list)
        checks.append(check)
        self.artifact["last_drain_terminated"] = result.terminated
        if not result.terminated:
            self.artifact.update(status="failed", reason_code="error_queue_incomplete")
            raise InstrumentError("scope error queue did not terminate within the configured bound")
        completed = self.artifact["completed_phases"]
        assert isinstance(completed, list)
        completed.append(phase)
        if records:
            self.artifact.update(
                status="completed",
                reason_code="records",
            )
            can_continue = (
                self.operation_spec.effect in {"observe", "stateful_read"}
                and self.error_spec.on_instrument_error == "record_and_continue"
            )
            if not can_continue:
                reason = (
                    "preexisting_instrument_error" if phase == "before" else "instrument_error"
                )
                self.artifact.update(status="failed", reason_code=reason)
                raise InstrumentError(
                    "instrument error records prevent the scope operation"
                )
        elif self.artifact.get("reason_code") != "records":
            self.artifact.update(status="completed", reason_code="empty")
        return records


def legacy_scope_error_artifact(values: Iterable[str], *, requested_limit: int) -> dict[str, object]:
    """Describe the old list[str] API without inventing typed drain evidence."""

    returned = tuple(values)
    return {
        "executor": "legacy_driver",
        "capability": "scope.errors",
        "status": "legacy_unstructured",
        "requested_limit": requested_limit,
        "returned_record_count": len(returned),
        "terminated": None,
        "query_count": None,
    }


__all__ = [
    "ScopeErrorPolicyExecutor",
    "legacy_scope_error_artifact",
    "resolve_error_check",
]
