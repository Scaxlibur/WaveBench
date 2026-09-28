"""Optional basic-state restoration contract for source plugins."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

from .models import SourceStatus


BASIC_RESTORE_FIELDS = frozenset({
    "output", "function", "frequency_hz", "amplitude", "amplitude_unit",
    "square_duty_cycle_percent",
})


@dataclass(frozen=True)
class SourceRestoreProfile:
    supported: bool
    operations: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    excluded_fields: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.supported) is not bool:
            raise ValueError("restore supported must be boolean")
        for name in ("operations", "fields", "excluded_fields"):
            values = getattr(self, name)
            if (not isinstance(values, tuple) or any(not isinstance(v, str) or not v or v.strip() != v for v in values)
                    or len(set(values)) != len(values)):
                raise ValueError(f"restore {name} must contain unique nonempty strings")
        if any(not op.startswith("source.") for op in self.operations):
            raise ValueError("restore operations must be source operation names")
        if set(self.fields) - SourceStatus.__dataclass_fields__.keys():
            raise ValueError("restore fields must name SourceStatus fields")
        if set(self.fields) & set(self.excluded_fields):
            raise ValueError("restored and excluded fields overlap")
        if self.supported:
            if not self.operations or not BASIC_RESTORE_FIELDS.issubset(self.fields):
                raise ValueError("supported restore requires operations and all basic fields")
        elif self.operations or self.fields:
            raise ValueError("unsupported restore cannot promise operations or fields")

    def as_dict(self):
        return {"schema": "wavebench.source_restore.v1", **asdict(self)}


@runtime_checkable
class SourceBasicRestoreDriver(Protocol):
    def snapshot_basic_state(self, channel: int) -> SourceStatus: ...

    def restore_basic_state(self, snapshot: SourceStatus) -> SourceStatus: ...
