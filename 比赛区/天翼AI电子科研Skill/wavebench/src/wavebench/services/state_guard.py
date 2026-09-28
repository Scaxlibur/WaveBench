"""Compare-before-write guards for source and power run sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isclose
from typing import Any, Mapping

from wavebench.errors import StateDriftError


@dataclass
class _BaseStateGuard:
    """Track fields controlled by WaveBench; measurements are intentionally excluded."""

    expected_by_channel: dict[int, dict[str, Any]] = field(default_factory=dict)

    def observe(self, status: Any) -> None:
        channel = _channel(status)
        if channel not in self.expected_by_channel:
            self.expected_by_channel[channel] = self._canonical(status)

    def before_write(self, status: Any, *, force_off: bool = False) -> None:
        channel = _channel(status)
        actual = self._canonical(status)
        expected = self.expected_by_channel.get(channel)
        if expected is None:
            self.expected_by_channel[channel] = actual
            return
        diff = _state_diff(expected, actual)
        if diff and not force_off:
            raise StateDriftError(
                f"{self.kind} channel {channel} changed outside the current run; refusing write",
                expected=expected,
                actual=actual,
                diff=diff,
            )
        if force_off and diff:
            # An authorized emergency OFF may converge from an externally
            # changed state; the post-write status becomes the new baseline.
            self.expected_by_channel[channel] = actual

    def after_write(self, status: Any) -> None:
        self.expected_by_channel[_channel(status)] = self._canonical(status)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {str(channel): dict(value) for channel, value in self.expected_by_channel.items()}

    @property
    def kind(self) -> str:
        return "instrument"

    def _canonical(self, status: Any) -> dict[str, Any]:
        raise NotImplementedError


@dataclass
class SourceStateGuard(_BaseStateGuard):
    @property
    def kind(self) -> str:
        return "source"

    def _canonical(self, status: Any) -> dict[str, Any]:
        data = _status_dict(status)
        return {
            key: data.get(key)
            for key in (
                "channel",
                "output",
                "function",
                "frequency_hz",
                "amplitude",
                "amplitude_unit",
                "offset_v",
                "phase_deg",
                "frequency_mode",
                "sweep_enabled",
                "apply_raw",
                "square_duty_cycle_percent",
            )
        }


@dataclass
class PowerStateGuard(_BaseStateGuard):
    @property
    def kind(self) -> str:
        return "power"

    def _canonical(self, status: Any) -> dict[str, Any]:
        data = _status_dict(status)
        return {
            key: data.get(key)
            for key in (
                "channel",
                "output",
                "mode",
                "rating",
                "set_voltage_v",
                "set_current_a",
            )
        }


def _channel(status: Any) -> int:
    value = _status_dict(status).get("channel")
    if type(value) is not int:
        raise StateDriftError("state guard status is missing an integer channel")
    return value


def _status_dict(status: Any) -> dict[str, Any]:
    as_dict = getattr(status, "as_dict", None)
    value = as_dict() if callable(as_dict) else getattr(status, "__dict__", None)
    if not isinstance(value, Mapping):
        raise StateDriftError("state guard requires a structured status snapshot")
    return dict(value)


def _state_diff(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key in sorted(set(expected) | set(actual)):
        expected_value = expected.get(key)
        actual_value = actual.get(key)
        if _values_match(expected_value, actual_value):
            continue
        diff[key] = {"expected": expected_value, "actual": actual_value}
    return diff


def _values_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return isclose(float(expected), float(actual), rel_tol=1.0e-6, abs_tol=1.0e-6)
    return expected == actual


__all__ = ["PowerStateGuard", "SourceStateGuard"]
