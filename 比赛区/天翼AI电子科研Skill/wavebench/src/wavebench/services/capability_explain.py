"""Offline explanations for operation capability and access decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wavebench.instruments.api import InstrumentDescriptor
from wavebench.services.access_policy import AccessMode, access_policy
from wavebench.services.operation_specs import OperationSpec, get_operation_spec


@dataclass(frozen=True)
class CapabilityExplanation:
    operation: str
    status: str
    reason: str
    spec: OperationSpec | None = None
    driver_id: str | None = None
    instrument_kind: str | None = None
    access: AccessMode = "read_write"
    available_capabilities: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    missing_optional_capabilities: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation": self.operation,
            "status": self.status,
            "reason": self.reason,
            "driver_id": self.driver_id,
            "instrument_kind": self.instrument_kind,
            "access": self.access,
            "available_capabilities": list(self.available_capabilities),
            "missing_capabilities": list(self.missing_capabilities),
            "missing_optional_capabilities": list(self.missing_optional_capabilities),
        }
        if self.spec is not None:
            payload["spec"] = self.spec.as_dict()
        return payload


def explain_operation(
    operation: str,
    *,
    descriptor: InstrumentDescriptor | Any | None = None,
    access: AccessMode = "read_write",
) -> CapabilityExplanation:
    """Explain one operation without opening a transport or instrument session."""

    spec = get_operation_spec(operation)
    if spec is None:
        return CapabilityExplanation(
            operation=operation,
            status="unknown_operation",
            reason="operation is not registered in the central OperationSpec registry",
            access=access,
        )

    driver_id = getattr(descriptor, "driver_id", None)
    kind = getattr(descriptor, "kind", None)
    available = tuple(sorted(set(getattr(descriptor, "capabilities", ())))) if descriptor else ()
    missing = tuple(sorted(set(spec.required_capabilities) - set(available)))
    missing_optional = tuple(
        sorted(set(spec.optional_capabilities) - set(available))
    )
    if spec.instrument_kind is not None and descriptor is None:
        return CapabilityExplanation(
            operation=operation,
            status="driver_required",
            reason="an instrument driver or configured instrument is required for capability evaluation",
            spec=spec,
            instrument_kind=spec.instrument_kind,
            access=access,
        )
    if spec.instrument_kind is not None and kind != spec.instrument_kind:
        return CapabilityExplanation(
            operation=operation,
            status="kind_mismatch",
            reason=f"operation requires instrument kind {spec.instrument_kind!r}",
            spec=spec,
            driver_id=driver_id,
            instrument_kind=kind,
            access=access,
            available_capabilities=available,
            missing_capabilities=missing,
            missing_optional_capabilities=missing_optional,
        )

    policy = access_policy(access)
    if not policy.allows(spec):
        status = "access_denied"
        reason = f"access policy {access!r} blocks effect {spec.effect!r}"
    elif missing:
        status = "missing_capability"
        reason = "driver does not declare all required capabilities"
    else:
        status = "supported"
        reason = "operation is supported by the selected driver and access policy"
    return CapabilityExplanation(
        operation=operation,
        status=status,
        reason=reason,
        spec=spec,
        driver_id=driver_id,
        instrument_kind=kind,
        access=access,
        available_capabilities=available,
        missing_capabilities=missing,
        missing_optional_capabilities=missing_optional,
    )


__all__ = ["CapabilityExplanation", "explain_operation"]
