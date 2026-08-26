"""Offline execution-intent generation and verification for run plans."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from wavebench.config import WaveBenchConfig
from wavebench.errors import ExecutionIntentError
from wavebench.services.frequency_response_evidence import digest, plan_digest
from wavebench.services.operation_specs import get_operation_spec
from wavebench.services.resource_lease import resource_fingerprint
from wavebench.services.run_plan import RunPlan


INTENT_SCHEMA = "wavebench.execution_intent.v1"

_STEP_OPERATIONS = {
    "scope.auto": "scope.autoscale",
    "scope.capture": "scope.capture",
    "sweep.frequency_response": "scope.capture_waveforms",
    "source.status": "source.status",
    "rf_source.status": "rf_source.snapshot",
    "rf_source.set_frequency": "rf_source.set_frequency",
    "rf_source.set_power_dbm": "rf_source.set_power_dbm",
    "rf_source.output_enable": "rf_source.output_enable",
    "rf_source.output_disable": "rf_source.output_disable",
    "source.arb_load": "source.arbitrary_upload",
    "source.set_freq": "source.set_frequency",
    "source.set_func": "source.set_function",
    "source.set_vpp": "source.set_amplitude_vpp",
    "source.set_duty": "source.set_square_duty_cycle",
    "source.output": "source.output",
    "source.basic_configure_v2": "source.basic_configure_v2",
    "source.output_enable_v2": "source.output_enable_v2",
    "source.output_disable_v2": "source.output_disable_v2",
    "source.harmonics_configure_v2": "source.harmonics_configure_v2",
    "source.harmonics_disable_v2": "source.harmonics_disable_v2",
    "source.modulation_configure_v2": "source.modulation_configure_v2",
    "source.modulation_pm_configure_v2": "source.modulation_pm_configure_v2",
    "source.modulation_fm_configure_v2": "source.modulation_fm_configure_v2",
    "source.modulation_pwm_configure_v2": "source.modulation_pwm_configure_v2",
    "source.sweep_configure_v2": "source.sweep_configure_v2",
    "source.burst_configure_v2": "source.burst_configure_v2",
    "source.pulse_configure_v2": "source.pulse_configure_v2",
    "source.arbitrary_storage_v2": "source.arbitrary_storage_v2",
    "source.arbitrary_select_v2": "source.arbitrary_select_v2",
    "source.combine_configure_v2": "source.combine_configure_v2",
    "source.coupling_configure_v2": "source.coupling_configure_v2",
    "source.tracking_configure_v2": "source.tracking_configure_v2",
    "source.phase_relation_configure_v2": "source.phase_relation_configure_v2",
    "power.status": "power.status",
    "power.set": "power.set_voltage_current_limit",
    "power.output": "power.output",
    "dmm.read": "dmm.read",
    "sleep": "run.sleep",
}


@dataclass(frozen=True)
class ExecutionIntent:
    plan_digest: str
    config_digest: str
    payloads: tuple[dict[str, Any], ...]
    operations: tuple[dict[str, Any], ...]
    safety: Mapping[str, Any]
    restore: Mapping[str, Any]
    intent_digest: str

    @property
    def schema(self) -> str:
        return INTENT_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "intent_digest": self.intent_digest,
            "plan_digest": self.plan_digest,
            "config_digest": self.config_digest,
            "payloads": [dict(item) for item in self.payloads],
            "operations": [dict(item) for item in self.operations],
            "safety": dict(self.safety),
            "restore": dict(self.restore),
        }


def build_execution_intent(plan: RunPlan, config: WaveBenchConfig) -> ExecutionIntent:
    plan_hash = plan_digest(plan)
    config_hash = digest(_config_semantics(config))
    payloads: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    for step in plan.steps:
        operation_name = _STEP_OPERATIONS.get(step.kind, step.kind)
        spec = get_operation_spec(operation_name)
        fields = dict(step.fields)
        payload_ref = _payload_reference(
            fields,
            payloads,
            base_directory=plan.path.parent,
        )
        if payload_ref is not None:
            fields["file"] = payload_ref["name"]
            fields["payload_digest"] = payload_ref["sha256"]
        entry: dict[str, Any] = {
            "step_index": step.index,
            "step_kind": step.kind,
            "operation": operation_name,
            "instrument_kind": spec.instrument_kind if spec else None,
            "effect": spec.effect if spec else None,
            "lease_mode": spec.lease_mode if spec else None,
            "changed_fields": list(spec.changed_fields) if spec else [],
            "restore_coverage": spec.restore_coverage if spec else "none",
            "session_purpose": spec.session_purpose if spec else "normal",
            "required_verified_fields": (
                list(spec.required_verified_fields) if spec else []
            ),
            "verification_fields": list(spec.verification_fields) if spec else [],
            "timeout_source": spec.timeout_source if spec else "connection.timeout_ms",
            "risk_flags": list(spec.risk_flags) if spec else [],
            "parameters": _safe_parameters(fields),
            "policy": {
                "on_failure": fields.get("on_failure", "stop"),
                "safety_gate": _safe_parameters(fields.get("safety_gate", {})),
            },
        }
        operations.append(entry)

    safety = _safe_parameters(asdict(plan.safety))
    restore = _safe_parameters(asdict(plan.restore))
    body = {
        "plan_digest": plan_hash,
        "config_digest": config_hash,
        "payloads": payloads,
        "operations": operations,
        "safety": safety,
        "restore": restore,
    }
    return ExecutionIntent(
        plan_digest=plan_hash,
        config_digest=config_hash,
        payloads=tuple(payloads),
        operations=tuple(operations),
        safety=safety,
        restore=restore,
        intent_digest=digest(body, length=32),
    )


def write_execution_intent(intent: ExecutionIntent, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(intent.as_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output


def load_execution_intent(path: str | Path) -> dict[str, Any]:
    intent_path = Path(path)
    try:
        payload = json.loads(intent_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionIntentError(f"failed to read execution intent: {intent_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != INTENT_SCHEMA:
        raise ExecutionIntentError(
            f"execution intent must use schema {INTENT_SCHEMA}: {intent_path}"
        )
    return payload


def verify_execution_intent(
    expected: Mapping[str, Any],
    plan: RunPlan,
    config: WaveBenchConfig,
) -> ExecutionIntent:
    current = build_execution_intent(plan, config)
    expected_digest = expected.get("intent_digest")
    if expected_digest != current.intent_digest:
        raise ExecutionIntentError(
            "execution intent does not match the current plan, configuration, or payloads",
            expected_digest=str(expected_digest) if expected_digest is not None else None,
            actual_digest=current.intent_digest,
            expected_plan_digest=expected.get("plan_digest"),
            actual_plan_digest=current.plan_digest,
            expected_config_digest=expected.get("config_digest"),
            actual_config_digest=current.config_digest,
        )
    return current


def _payload_reference(
    fields: Mapping[str, Any],
    payloads: list[dict[str, Any]],
    *,
    base_directory: Path,
) -> dict[str, Any] | None:
    raw_path = fields.get("file")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = base_directory / path
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ExecutionIntentError(f"execution intent payload is unreadable: {path}") from exc
    reference = {
        "name": path.name,
        "sha256": sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    payloads.append(reference)
    return reference


def _config_semantics(config: WaveBenchConfig) -> dict[str, Any]:
    raw = asdict(config) if is_dataclass(config) else {}
    return _safe_parameters(raw, redact_resources=True)


def _safe_parameters(value: Any, *, redact_resources: bool = False) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {
            str(key): (
                resource_fingerprint(str(item))
                if redact_resources and key == "resource" and item
                else _safe_parameters(item, redact_resources=redact_resources)
            )
            for key, item in value.items()
            if key not in {"source_path"}
        }
    if isinstance(value, (list, tuple)):
        return [_safe_parameters(item, redact_resources=redact_resources) for item in value]
    if isinstance(value, Path):
        return value.name
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = [
    "ExecutionIntent",
    "INTENT_SCHEMA",
    "build_execution_intent",
    "load_execution_intent",
    "verify_execution_intent",
    "write_execution_intent",
]
