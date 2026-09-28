from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from wavebench.instruments.models import SourceStatus
from wavebench.errors import DataError


@dataclass(frozen=True)
class RestorableSourceState:
    channel: int
    output: str
    function: str
    frequency_hz: float
    amplitude_vpp: float
    amplitude_unit: str
    square_duty_cycle_percent: float | None = None
    driver_snapshot: SourceStatus | None = field(default=None, repr=False, compare=False)
    driver_id: str | None = field(default=None, repr=False)
    instrument_idn: str | None = field(default=None, repr=False)
    restore_evidence: dict | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_status(cls, status: SourceStatus) -> "RestorableSourceState":
        if status.frequency_hz is None:
            raise DataError("cannot snapshot source state: frequency_hz is missing")
        if status.amplitude is None:
            raise DataError("cannot snapshot source state: amplitude is missing")
        if not status.amplitude_unit:
            raise DataError("cannot snapshot source state: amplitude_unit is missing")
        if status.amplitude_unit.strip().upper() != "VPP":
            raise DataError("cannot snapshot source state: only VPP amplitude is restorable for now")
        if type(status.channel) is not int or status.channel < 1 or status.output.strip().upper() not in {"ON", "OFF"}:
            raise DataError("cannot snapshot source state: invalid channel or output")
        for value in (status.frequency_hz, status.amplitude):
            if type(value) not in (int, float) or not isfinite(value):
                raise DataError("cannot snapshot source state: nonfinite numeric value")
        if status.frequency_hz <= 0 or status.amplitude < 0 or not status.function.strip():
            raise DataError("cannot snapshot source state: invalid basic value")
        return cls(
            channel=status.channel,
            output=status.output.strip().upper(),
            function=status.function.strip().upper(),
            frequency_hz=status.frequency_hz,
            amplitude_vpp=status.amplitude,
            amplitude_unit=status.amplitude_unit.strip().upper(),
            square_duty_cycle_percent=status.square_duty_cycle_percent,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "output": self.output,
            "function": self.function,
            "frequency_hz": self.frequency_hz,
            "amplitude_vpp": self.amplitude_vpp,
            "amplitude_unit": self.amplitude_unit,
            "square_duty_cycle_percent": self.square_duty_cycle_percent,
        }
