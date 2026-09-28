from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from wavebench.errors import DataError


class DG4000ByteOrder(StrEnum):
    BIG = "big"
    LITTLE = "little"


@dataclass(frozen=True)
class DG4000DacBlock:
    """Validated DG4000 DATA:DAC command shared with source-driver plugins."""

    command: bytes
    points: int
    data_bytes: int
    byte_order: DG4000ByteOrder

    @property
    def header(self) -> bytes:
        prefix = b":DATA:DAC VOLATILE,"
        if not self.command.startswith(prefix):
            raise DataError("unexpected DG4000 DAC command prefix")
        payload = self.command[len(prefix):]
        if not payload.startswith(b"#"):
            raise DataError("unexpected DG4000 DAC binary block header")
        digits = int(chr(payload[1]))
        return payload[: 2 + digits]
