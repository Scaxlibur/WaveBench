"""Portable proof returned by a driver-owned single, frozen multi-channel transaction."""
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from .models import WaveformData


@dataclass(frozen=True)
class SynchronizedCapture:
    waveforms: dict[int, WaveformData]
    synchronization: dict[str, Any]


@runtime_checkable
class SynchronizedScopeDriver(Protocol):
    def capture_synchronized(self, *, channels: list[int], points: str = 'DEF',
                             check_errors: bool = True, time_range_s: float | None = None,
                             vertical_scale_v_per_div: float | None = None,
                             on_waveform: Callable[[int, WaveformData], None] | None = None) -> SynchronizedCapture: ...
