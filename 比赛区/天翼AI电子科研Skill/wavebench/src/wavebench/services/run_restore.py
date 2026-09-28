from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from wavebench.errors import SessionHealthError, TransportIOError, error_envelope
from wavebench.services.run_plan import RunPlan
from wavebench.services.source_state import RestorableSourceState


class SourceRestoreService(Protocol):
    def snapshot_restorable_state(self, channel: int | None = None) -> RestorableSourceState: ...

    def restore_restorable_state(self, state: RestorableSourceState) -> object: ...


SourceServiceFactory = Callable[[], SourceRestoreService]


def snapshot_source_state(
    plan: RunPlan,
    *,
    source_service_factory: SourceServiceFactory,
) -> list[RestorableSourceState] | None:
    if not plan.restore.source_state:
        return None
    service = source_service_factory()
    channels = plan.restore.source_channels or (None,)
    return [service.snapshot_restorable_state(channel=channel) for channel in channels]


def restore_source_state(
    states: list[RestorableSourceState] | None,
    *,
    force_off_channels: tuple[int, ...] = (),
    source_service_factory: SourceServiceFactory,
) -> dict[str, Any] | None:
    if not states:
        return None
    errors: list[dict[str, str | int | None]] = []
    service = source_service_factory()
    for state in states:
        try:
            target = state
            if state.channel in force_off_channels:
                from dataclasses import replace
                target = replace(state, output="OFF")
                if getattr(state, "restore_evidence", None) is not None:
                    state.restore_evidence["output_override"] = "safety_gate_off"
            service.restore_restorable_state(target)
        except (TransportIOError, SessionHealthError) as exc:
            # A gated/structured transport failure is authoritative.  Do not
            # issue further restore writes on an uncertain or poisoned epoch.
            envelope = error_envelope(
                exc,
                operation=f"restore.source.{state.channel}",
            )
            if getattr(state, "restore_evidence", None) is not None:
                state.restore_evidence.update(status="failed", error=envelope)
            errors.append(
                {
                    "channel": state.channel,
                    "type": type(exc).__name__,
                    "message": envelope["message"],
                    "error": envelope,
                }
            )
            break
        except Exception as exc:  # pragma: no cover - defensive, covered through mocks
            envelope = error_envelope(
                exc,
                operation=f"restore.source.{state.channel}",
            )
            if getattr(state, "restore_evidence", None) is not None:
                state.restore_evidence.update(status="failed", error=envelope)
            errors.append(
                {
                    "channel": state.channel,
                    "type": type(exc).__name__,
                    "message": envelope["message"],
                    "error": envelope,
                }
            )
    if errors:
        return {
            "type": "RestoreError",
            "message": "source state restore failed",
            "errors": errors,
        }
    return None


def source_restore_coverage(plan, descriptor, default_channel):
    """Offline declaration check; no session or instrument I/O."""
    from wavebench.errors import ConfigError
    from wavebench.services.execution_intent import _STEP_OPERATIONS
    from wavebench.services.operation_specs import get_operation_spec

    profile = getattr(descriptor, "source_restore", None)
    source_steps = [step for step in plan.steps if step.kind.startswith("source.")]
    arbitrary = any(step.kind == "source.arb_load" for step in source_steps)
    if profile is None and not arbitrary:
        return None
    if plan.restore.source_state:
        if profile is None or not profile.supported:
            raise ConfigError("requested source restore is not supported by the plugin for this plan")
        for step in source_steps:
            operation = _STEP_OPERATIONS.get(step.kind, step.kind)
            spec = get_operation_spec(operation)
            if spec is not None and spec.effect == "write" and operation not in profile.operations:
                raise ConfigError(f"source restore is not declared after {operation}")
        if arbitrary:
            restored_channels = set(plan.restore.source_channels or (default_channel,))
            if any(step.fields.get("channel", default_channel) not in restored_channels
                   for step in source_steps if step.kind == "source.arb_load"):
                raise ConfigError("arbitrary upload channel must be included in source restore channels")
    return {
        "requested": plan.restore.source_state,
        "declaration": profile.as_dict() if profile is not None else {"supported": None},
        "uncovered": list(profile.excluded_fields) if profile else ["arbitrary_payload"],
    }
