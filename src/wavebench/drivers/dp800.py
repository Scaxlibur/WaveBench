from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from threading import RLock
import time

from wavebench.errors import (
    DataError,
    InstrumentError,
    SessionHealthError,
    TransportIOError,
)
from wavebench.instruments.models import PowerMeasurement, PowerProtectionStatus, PowerStatus
from wavebench.transport.contracts import ReplayPolicy


DP800_MODEL_CHANNELS = {
    "DP811": 1,
    "DP811A": 1,
    "DP821": 2,
    "DP821A": 2,
    "DP831": 3,
    "DP831A": 3,
    "DP832": 3,
    "DP832A": 3,
}
DP800_WRITE_MODELS = {"DP832", "DP832A"}
_APPLY_TARGET_PATTERN = re.compile(
    r"^CH(?P<channel>[1-3]):(?P<rating>(?:[PN]|-)?\d+(?:\.\d+)?V/\d+(?:\.\d+)?A)$",
    re.IGNORECASE,
)


def parse_idn_model(response: str) -> tuple[str, int]:
    parts = [part.strip() for part in response.strip().split(",")]
    if len(parts) != 4 or parts[0].upper() != "RIGOL TECHNOLOGIES":
        raise DataError("unexpected DP800 *IDN? response")
    model = parts[1].upper()
    try:
        return model, DP800_MODEL_CHANNELS[model]
    except KeyError as exc:
        raise DataError(f"unsupported DP800 model: {model!r}") from exc


def _finite_float(value: str, *, field: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise DataError(f"invalid DP800 {field}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise DataError(f"DP800 {field} must be finite")
    return parsed


def _enum_response(response: str, *, field: str, allowed: set[str]) -> str:
    value = response.strip().upper()
    if value not in allowed:
        expected = ", ".join(sorted(allowed))
        raise DataError(f"unexpected DP800 {field}: {value!r}; expected one of: {expected}")
    return value


def parse_apply_response(
    response: str,
    *,
    expected_channel: int | None = None,
    allow_targetless: bool = False,
) -> tuple[str | None, float, float]:
    parts = [part.strip() for part in response.strip().split(",")]
    if len(parts) == 2:
        if ":" in parts[0]:
            raise DataError(f"unexpected DP800 APPL? response: {response!r}")
        if not allow_targetless or expected_channel not in (None, 1):
            raise DataError(
                "unexpected DP800 APPL? response without a confirmed single-channel target"
            )
        return (
            None,
            _finite_float(parts[0], field="set voltage"),
            _finite_float(parts[1], field="set current"),
        )
    if len(parts) != 3:
        raise DataError(f"unexpected DP800 APPL? response: {response!r}")
    target = _APPLY_TARGET_PATTERN.fullmatch(parts[0])
    if target is None:
        raise DataError(f"unexpected DP800 APPL? target: {parts[0]!r}")
    channel = int(target.group("channel"))
    if expected_channel is not None and channel != expected_channel:
        raise DataError(
            f"unexpected DP800 APPL? channel: CH{channel}; expected CH{expected_channel}"
        )
    return (
        target.group("rating").upper(),
        _finite_float(parts[1], field="set voltage"),
        _finite_float(parts[2], field="set current"),
    )


def parse_measure_all_response(response: str) -> tuple[float, float, float]:
    parts = [part.strip() for part in response.strip().split(",")]
    if len(parts) != 3:
        raise DataError(f"unexpected DP800 MEAS:ALL? response: {response!r}")
    return (
        _finite_float(parts[0], field="measured voltage"),
        _finite_float(parts[1], field="measured current"),
        _finite_float(parts[2], field="measured power"),
    )


def parse_protection_value_response(response: str) -> float:
    return _finite_float(response.strip(), field="protection threshold")


class _AmbiguousWriteError(InstrumentError):
    """A transport write failed after its device-side outcome became unknowable."""


@dataclass
class DP800Power:
    transport: object
    check_errors_after_ops: bool = True
    _io_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _configuration_writes_blocked: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._model: str | None = None
        self._channel_count: int | None = None

    def idn(self) -> str:
        with self._io_lock:
            response = self.transport.query("*IDN?", replay=ReplayPolicy.NO_REPLAY)
            self._model, self._channel_count = parse_idn_model(response)
            return response

    def _validate_channel(self, channel: int) -> None:
        if type(channel) is not int or channel < 1:
            raise DataError("channel must be an integer >= 1")
        if self._channel_count is None:
            self.idn()
        assert self._channel_count is not None
        if channel > self._channel_count:
            raise DataError(
                f"channel CH{channel} is unavailable on {self._model}; "
                f"valid channels are 1..{self._channel_count}"
            )

    def _validate_write_channel(self, channel: int) -> None:
        self._validate_channel(channel)
        if self._model not in DP800_WRITE_MODELS:
            raise DataError(
                f"DP800 writes are supported only on validated DP832/DP832A models; "
                f"detected {self._model}"
            )

    def _check_errors_enabled(self, check_errors: bool | None) -> bool:
        if check_errors is None:
            return self.check_errors_after_ops
        if type(check_errors) is not bool:
            raise DataError("check_errors must be a boolean or None")
        return check_errors

    @property
    def configuration_writes_blocked(self) -> bool:
        with self._io_lock:
            return self._configuration_writes_blocked

    def _require_configuration_writes(self) -> None:
        if self._configuration_writes_blocked:
            raise InstrumentError(
                "DP800 configuration writes are blocked after an earlier ambiguous transaction"
            )

    @staticmethod
    def _matches(actual: float, expected: float, *, resolution: float) -> bool:
        return math.isclose(
            actual,
            expected,
            rel_tol=0.0,
            abs_tol=(resolution / 2.0) + 1e-12,
        )

    def _voltage_matches(self, actual: float, expected: float) -> bool:
        resolution = 0.001 if self._model == "DP832A" else 0.01
        return self._matches(actual, expected, resolution=resolution)

    def _current_matches(self, actual: float, expected: float) -> bool:
        return self._matches(actual, expected, resolution=0.001)

    def errors(self, limit: int = 8) -> list[str]:
        with self._io_lock:
            errors: list[str] = []
            for _ in range(limit):
                response = self.transport.query("SYST:ERR?", replay=ReplayPolicy.NO_REPLAY)
                errors.append(response)
                if response.startswith("0") or "No error" in response:
                    break
            return errors

    def assert_no_errors(self) -> None:
        errors = self.errors()
        active = [item for item in errors if not (item.startswith("0") or "No error" in item)]
        if active:
            raise InstrumentError("instrument error queue is not empty: " + "; ".join(active))

    def get_status(self, channel: int) -> PowerStatus:
        with self._io_lock:
            self._validate_channel(channel)
            single_channel = self._channel_count == 1
            rating, set_voltage_v, set_current_a = parse_apply_response(
                self.transport.query(":APPL?" if single_channel else f":APPL? CH{channel}", replay=ReplayPolicy.NO_REPLAY),
                expected_channel=channel,
                allow_targetless=single_channel,
            )
            measurement = self.get_measurement(channel)
            return PowerStatus(
                channel=channel,
                output=_enum_response(
                    self.transport.query(f":OUTP? CH{channel}", replay=ReplayPolicy.NO_REPLAY),
                    field="output state",
                    allowed={"OFF", "ON"},
                ),
                mode=_enum_response(
                    self.transport.query(f":OUTP:MODE? CH{channel}", replay=ReplayPolicy.NO_REPLAY),
                    field="output mode",
                    allowed={"CC", "CV", "UR"},
                ),
                rating=rating,
                set_voltage_v=set_voltage_v,
                set_current_a=set_current_a,
                measured_voltage_v=measurement.measured_voltage_v,
                measured_current_a=measurement.measured_current_a,
                measured_power_w=measurement.measured_power_w,
            )

    def get_measurement(self, channel: int) -> PowerMeasurement:
        with self._io_lock:
            self._validate_channel(channel)
            measured_voltage_v, measured_current_a, measured_power_w = parse_measure_all_response(
                self.transport.query(f":MEAS:ALL? CH{channel}", replay=ReplayPolicy.NO_REPLAY)
            )
            return PowerMeasurement(
                channel=channel,
                measured_voltage_v=measured_voltage_v,
                measured_current_a=measured_current_a,
                measured_power_w=measured_power_w,
            )

    def get_protection_status(self, channel: int) -> PowerProtectionStatus:
        with self._io_lock:
            self._validate_channel(channel)
            return PowerProtectionStatus(
                channel=channel,
                ovp_enabled=_enum_response(
                    self.transport.query(f":OUTP:OVP? CH{channel}", replay=ReplayPolicy.NO_REPLAY),
                    field="OVP state",
                    allowed={"OFF", "ON"},
                ),
                ovp_threshold_v=parse_protection_value_response(
                    self.transport.query(f":OUTP:OVP:VAL? CH{channel}", replay=ReplayPolicy.NO_REPLAY)
                ),
                ovp_tripped=_enum_response(
                    self.transport.query(f":OUTP:OVP:QUES? CH{channel}", replay=ReplayPolicy.NO_REPLAY),
                    field="OVP trip state",
                    allowed={"NO", "YES"},
                ),
                ocp_enabled=_enum_response(
                    self.transport.query(f":OUTP:OCP? CH{channel}", replay=ReplayPolicy.NO_REPLAY),
                    field="OCP state",
                    allowed={"OFF", "ON"},
                ),
                ocp_threshold_a=parse_protection_value_response(
                    self.transport.query(f":OUTP:OCP:VAL? CH{channel}", replay=ReplayPolicy.NO_REPLAY)
                ),
                ocp_tripped=_enum_response(
                    self.transport.query(f":OUTP:OCP:QUES? CH{channel}", replay=ReplayPolicy.NO_REPLAY),
                    field="OCP trip state",
                    allowed={"NO", "YES"},
                ),
            )

    def _protection_matches(
        self,
        actual: PowerProtectionStatus,
        expected: PowerProtectionStatus,
        *,
        include_trip: bool,
    ) -> bool:
        matches = (
            actual.ovp_enabled == expected.ovp_enabled
            and self._voltage_matches(actual.ovp_threshold_v, expected.ovp_threshold_v)
            and actual.ocp_enabled == expected.ocp_enabled
            and self._current_matches(actual.ocp_threshold_a, expected.ocp_threshold_a)
        )
        if include_trip:
            matches = matches and actual.ovp_tripped == expected.ovp_tripped
            matches = matches and actual.ocp_tripped == expected.ocp_tripped
        return matches

    def _write_protection_value(
        self,
        command: str,
        query: str,
        target: float,
        *,
        value_kind: str,
    ) -> None:
        try:
            self.transport.write(command)
        except (TransportIOError, SessionHealthError):
            raise
        except Exception as exc:
            raise _AmbiguousWriteError(f"DP800 write outcome is ambiguous: {command}") from exc
        actual = parse_protection_value_response(self.transport.query(query, replay=ReplayPolicy.NO_REPLAY))
        matches = (
            self._voltage_matches(actual, target)
            if value_kind == "voltage"
            else self._current_matches(actual, target)
        )
        if not matches:
            raise DataError(f"DP800 protection readback mismatch for {query}")

    def _write_protection_state(self, command: str, query: str, target: str) -> None:
        try:
            self.transport.write(command)
        except (TransportIOError, SessionHealthError):
            raise
        except Exception as exc:
            raise _AmbiguousWriteError(f"DP800 write outcome is ambiguous: {command}") from exc
        actual = _enum_response(
            self.transport.query(query, replay=ReplayPolicy.NO_REPLAY),
            field="protection state",
            allowed={"OFF", "ON"},
        )
        if actual != target:
            raise DataError(f"DP800 protection readback mismatch for {query}")

    def _apply_protection_pair(
        self,
        channel: int,
        *,
        kind: str,
        current_enabled: str,
        current_threshold: float,
        target_enabled: str,
        target_threshold: float,
    ) -> None:
        prefix = f":OUTP:{kind}"
        value_kind = "voltage" if kind == "OVP" else "current"
        threshold_matches = (
            self._voltage_matches(current_threshold, target_threshold)
            if value_kind == "voltage"
            else self._current_matches(current_threshold, target_threshold)
        )
        if current_enabled == "ON" and target_enabled == "OFF":
            self._write_protection_state(
                f"{prefix} CH{channel},OFF",
                f"{prefix}? CH{channel}",
                "OFF",
            )
            current_enabled = "OFF"
        if not threshold_matches:
            self._write_protection_value(
                f"{prefix}:VAL CH{channel},{target_threshold:.12g}",
                f"{prefix}:VAL? CH{channel}",
                target_threshold,
                value_kind=value_kind,
            )
        if current_enabled == "OFF" and target_enabled == "ON":
            self._write_protection_state(
                f"{prefix} CH{channel},ON",
                f"{prefix}? CH{channel}",
                "ON",
            )

    def _restore_protection(
        self,
        channel: int,
        previous: PowerProtectionStatus,
        *,
        check_errors: bool | None,
    ) -> None:
        current = self.get_protection_status(channel)
        self._apply_protection_pair(
            channel,
            kind="OVP",
            current_enabled=current.ovp_enabled,
            current_threshold=current.ovp_threshold_v,
            target_enabled=previous.ovp_enabled,
            target_threshold=previous.ovp_threshold_v,
        )
        self._apply_protection_pair(
            channel,
            kind="OCP",
            current_enabled=current.ocp_enabled,
            current_threshold=current.ocp_threshold_a,
            target_enabled=previous.ocp_enabled,
            target_threshold=previous.ocp_threshold_a,
        )
        restored = self.get_protection_status(channel)
        if not self._protection_matches(restored, previous, include_trip=True):
            raise DataError("DP800 protection restore mismatch or trip state changed")
        if self._check_errors_enabled(check_errors):
            self.assert_no_errors()

    def set_protection(
        self,
        channel: int,
        *,
        ovp_threshold_v: float | None = None,
        ovp_enabled: bool | None = None,
        ocp_threshold_a: float | None = None,
        ocp_enabled: bool | None = None,
        check_errors: bool | None = None,
    ) -> PowerProtectionStatus:
        with self._io_lock:
            self._require_configuration_writes()
            self._validate_write_channel(channel)
            if ovp_threshold_v is not None:
                _finite_float(str(ovp_threshold_v), field="OVP threshold")
            if ocp_threshold_a is not None:
                _finite_float(str(ocp_threshold_a), field="OCP threshold")
            if ovp_threshold_v is not None and ovp_threshold_v < 0:
                raise DataError("OVP threshold must be >= 0")
            if ocp_threshold_a is not None and ocp_threshold_a <= 0:
                raise DataError("OCP threshold must be > 0")
            if ovp_enabled is not None and type(ovp_enabled) is not bool:
                raise DataError("ovp_enabled must be a boolean or None")
            if ocp_enabled is not None and type(ocp_enabled) is not bool:
                raise DataError("ocp_enabled must be a boolean or None")

            previous = self.get_protection_status(channel)
            setpoints = self.get_status(channel)
            target = PowerProtectionStatus(
                channel=channel,
                ovp_enabled=(
                    "ON" if ovp_enabled else "OFF"
                ) if ovp_enabled is not None else previous.ovp_enabled,
                ovp_threshold_v=(
                    ovp_threshold_v
                    if ovp_threshold_v is not None
                    else previous.ovp_threshold_v
                ),
                ovp_tripped=previous.ovp_tripped,
                ocp_enabled=(
                    "ON" if ocp_enabled else "OFF"
                ) if ocp_enabled is not None else previous.ocp_enabled,
                ocp_threshold_a=(
                    ocp_threshold_a
                    if ocp_threshold_a is not None
                    else previous.ocp_threshold_a
                ),
                ocp_tripped=previous.ocp_tripped,
            )
            if self._protection_matches(previous, target, include_trip=True):
                if self._check_errors_enabled(check_errors):
                    self.assert_no_errors()
                return previous
            if target.ovp_enabled == "ON" and (
                target.ovp_threshold_v + 0.0005 < setpoints.set_voltage_v
            ):
                raise DataError("OVP threshold cannot be below the active voltage setpoint")
            if target.ocp_enabled == "ON" and (
                target.ocp_threshold_a + 0.0005 < setpoints.set_current_a
            ):
                raise DataError("OCP threshold cannot be below the active current limit")

            try:
                self._apply_protection_pair(
                    channel,
                    kind="OVP",
                    current_enabled=previous.ovp_enabled,
                    current_threshold=previous.ovp_threshold_v,
                    target_enabled=target.ovp_enabled,
                    target_threshold=target.ovp_threshold_v,
                )
                self._apply_protection_pair(
                    channel,
                    kind="OCP",
                    current_enabled=previous.ocp_enabled,
                    current_threshold=previous.ocp_threshold_a,
                    target_enabled=target.ocp_enabled,
                    target_threshold=target.ocp_threshold_a,
                )
                status = self.get_protection_status(channel)
                if not self._protection_matches(status, target, include_trip=True):
                    raise DataError(
                        "DP800 protection transaction readback mismatch or trip state changed"
                    )
                if self._check_errors_enabled(check_errors):
                    self.assert_no_errors()
                return status
            except (TransportIOError, SessionHealthError):
                # A gated or structured transport failure is already the
                # strongest evidence available; do not issue recovery I/O or
                # replace it with a transaction summary.
                raise
            except Exception as exc:
                ambiguous_write = isinstance(exc, _AmbiguousWriteError)
                try:
                    self._restore_protection(channel, previous, check_errors=check_errors)
                except (TransportIOError, SessionHealthError):
                    raise
                except Exception as restore_exc:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DP800 protection transaction failed and restoration is ambiguous; "
                        "configuration writes are blocked"
                    ) from restore_exc
                if ambiguous_write:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DP800 protection write outcome is ambiguous; the original configuration "
                        "was restored but configuration writes are blocked"
                    ) from exc
                raise InstrumentError(
                    "DP800 protection transaction failed; the original configuration was restored"
                ) from exc

    def set_voltage_current_limit(
        self,
        channel: int,
        voltage_v: float,
        current_limit_a: float,
        *,
        check_errors: bool | None = None,
        settle_ms_after_set: int = 0,
    ) -> PowerStatus:
        with self._io_lock:
            self._require_configuration_writes()
            self._validate_write_channel(channel)
            _finite_float(str(voltage_v), field="set voltage")
            _finite_float(str(current_limit_a), field="current limit")
            if voltage_v < 0:
                raise DataError("voltage must be >= 0")
            if current_limit_a <= 0:
                raise DataError("current limit must be > 0")

            previous = self.get_status(channel)
            if self._voltage_matches(previous.set_voltage_v, voltage_v) and self._current_matches(
                previous.set_current_a, current_limit_a
            ):
                if self._check_errors_enabled(check_errors):
                    self.assert_no_errors()
                return previous

            write_returned = False
            try:
                self.transport.write(f":APPL CH{channel},{voltage_v:.12g},{current_limit_a:.12g}")
                write_returned = True
                if settle_ms_after_set:
                    time.sleep(settle_ms_after_set / 1000.0)
                status = self.get_status(channel)
                if not self._voltage_matches(
                    status.set_voltage_v, voltage_v
                ) or not self._current_matches(status.set_current_a, current_limit_a):
                    raise DataError("DP800 APPL readback mismatch")
                if status.output != previous.output:
                    raise DataError("DP800 APPL unexpectedly changed output state")
                if self._check_errors_enabled(check_errors):
                    self.assert_no_errors()
                return status
            except (TransportIOError, SessionHealthError):
                raise
            except Exception as exc:
                try:
                    self.transport.write(
                        f":APPL CH{channel},{previous.set_voltage_v:.12g},"
                        f"{previous.set_current_a:.12g}"
                    )
                    if previous.output == "OFF":
                        self.transport.write(f":OUTP CH{channel},OFF")
                    restored = self.get_status(channel)
                    if not self._voltage_matches(
                        restored.set_voltage_v, previous.set_voltage_v
                    ) or not self._current_matches(
                        restored.set_current_a, previous.set_current_a
                    ):
                        raise DataError("DP800 APPL restore readback mismatch")
                    if restored.output != previous.output:
                        raise DataError("DP800 APPL restore changed output state")
                    if self._check_errors_enabled(check_errors):
                        self.assert_no_errors()
                except (TransportIOError, SessionHealthError):
                    raise
                except Exception as restore_exc:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DP800 APPL transaction failed and restoration is ambiguous; "
                        "configuration writes are blocked"
                    ) from restore_exc
                if not write_returned:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DP800 APPL write outcome is ambiguous; the original setpoint was restored "
                        "but configuration writes are blocked"
                    ) from exc
                raise InstrumentError(
                    "DP800 APPL transaction failed; the original setpoint was restored"
                ) from exc

    def set_output(
        self,
        channel: int,
        enabled: bool,
        *,
        check_errors: bool | None = None,
        settle_ms_after_output: int = 0,
    ) -> PowerStatus:
        with self._io_lock:
            self._require_configuration_writes()
            self._validate_write_channel(channel)
            if type(enabled) is not bool:
                raise DataError("enabled must be a boolean")
            previous = self.get_status(channel)
            target = "ON" if enabled else "OFF"
            if previous.output == target:
                if self._check_errors_enabled(check_errors):
                    self.assert_no_errors()
                return previous

            write_returned = False
            try:
                self.transport.write(f":OUTP CH{channel},{target}")
                write_returned = True
                if settle_ms_after_output:
                    time.sleep(settle_ms_after_output / 1000.0)
                status = self.get_status(channel)
                if status.output != target:
                    raise DataError("DP800 output readback mismatch")
                if self._check_errors_enabled(check_errors):
                    self.assert_no_errors()
                return status
            except (TransportIOError, SessionHealthError):
                raise
            except Exception as exc:
                try:
                    self.transport.write(f":OUTP CH{channel},OFF")
                    recovered = self.get_status(channel)
                    if recovered.output != "OFF":
                        raise DataError("DP800 output failed to converge to OFF")
                    if self._check_errors_enabled(check_errors):
                        self.assert_no_errors()
                except (TransportIOError, SessionHealthError):
                    raise
                except Exception as recovery_exc:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DP800 output transaction failed and OFF recovery is ambiguous; "
                        "configuration writes are blocked"
                    ) from recovery_exc
                if not write_returned:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DP800 output write outcome is ambiguous; output was forced OFF but "
                        "configuration writes are blocked"
                    ) from exc
                raise InstrumentError(
                    "DP800 output transaction failed; output was forced OFF"
                ) from exc

    def close(self) -> None:
        with self._io_lock:
            self.transport.close()
