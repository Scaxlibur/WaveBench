"""Instrument access policy checks shared by Service and transport layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from wavebench.errors import AccessDeniedError, ConfigError
from wavebench.services.operation_specs import OperationSpec


AccessMode = Literal["read_only", "read_write", "disabled"]
ACCESS_MODES = frozenset({"read_only", "read_write", "disabled"})


def normalize_access_mode(value: object, name: str) -> AccessMode:
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be read_only, read_write, or disabled")
    normalized = value.strip().lower()
    if normalized not in ACCESS_MODES:
        choices = ", ".join(sorted(ACCESS_MODES))
        raise ConfigError(f"{name} must be one of: {choices}")
    return normalized  # type: ignore[return-value]


@dataclass(frozen=True)
class AccessPolicy:
    mode: AccessMode = "read_write"

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_access_mode(self.mode, "access"))

    def allows(self, spec: OperationSpec) -> bool:
        if spec.effect == "offline":
            return True
        if self.mode == "disabled":
            return False
        if self.mode == "read_only":
            return spec.effect in {"observe", "stateful_read"}
        return True

    def require(self, spec: OperationSpec, *, operation: str | None = None) -> None:
        if self.allows(spec):
            return
        subject = operation or spec.operation
        raise AccessDeniedError(
            f"operation {subject!r} is blocked by access policy {self.mode!r}; "
            f"required effect: {spec.effect}"
        )


def access_policy(mode: object, name: str = "access") -> AccessPolicy:
    return AccessPolicy(normalize_access_mode(mode, name))
