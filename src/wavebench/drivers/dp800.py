from __future__ import annotations

from dataclasses import dataclass
import math
import re
import time

from wavebench.errors import DataError, InstrumentError
from wavebench.instruments.models import PowerMeasurement, PowerProtectionStatus, PowerStatus


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
) -> tuple[str | None, float, float]:
    parts = [part.strip() for part in response.strip().split(",")]
    if len(parts) == 2:
        if ":" in parts[0]:
            raise DataError(f"unexpected DP800 APPL? response: {response!r}")
        if expected_channel not in (None, 1):
            raise DataError(
                "unexpected DP800 APPL? response without channel target for "
                f"CH{expected_channel}"
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


@dataclass
class DP800Power:
    transport: object
    check_errors_after_ops: bool = True

    def __post_init__(self) -> None:
        self._model: str | None = None
        self._channel_count: int | None = None

    def idn(self) -> str:
        response = self.transport.query("*IDN?")
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

    def errors(self, limit: int = 8) -> list[str]:
        errors: list[str] = []
        for _ in range(limit):
            response = self.transport.query("SYST:ERR?")
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
        self._validate_channel(channel)
        single_channel = self._channel_count == 1
        rating, set_voltage_v, set_current_a = parse_apply_response(
            self.transport.query(":APPL?" if single_channel else f":APPL? CH{channel}"),
            expected_channel=channel,
        )
        measurement = self.get_measurement(channel)
        return PowerStatus(
            channel=channel,
            output=_enum_response(
                self.transport.query(f":OUTP? CH{channel}"),
                field="output state",
                allowed={"OFF", "ON"},
            ),
            mode=_enum_response(
                self.transport.query(f":OUTP:MODE? CH{channel}"),
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
        self._validate_channel(channel)
        measured_voltage_v, measured_current_a, measured_power_w = parse_measure_all_response(
            self.transport.query(f":MEAS:ALL? CH{channel}")
        )
        return PowerMeasurement(
            channel=channel,
            measured_voltage_v=measured_voltage_v,
            measured_current_a=measured_current_a,
            measured_power_w=measured_power_w,
        )

    def get_protection_status(self, channel: int) -> PowerProtectionStatus:
        self._validate_channel(channel)
        return PowerProtectionStatus(
            channel=channel,
            ovp_enabled=_enum_response(
                self.transport.query(f":OUTP:OVP? CH{channel}"),
                field="OVP state",
                allowed={"OFF", "ON"},
            ),
            ovp_threshold_v=parse_protection_value_response(self.transport.query(f":OUTP:OVP:VAL? CH{channel}")),
            ovp_tripped=_enum_response(
                self.transport.query(f":OUTP:OVP:QUES? CH{channel}"),
                field="OVP trip state",
                allowed={"NO", "YES"},
            ),
            ocp_enabled=_enum_response(
                self.transport.query(f":OUTP:OCP? CH{channel}"),
                field="OCP state",
                allowed={"OFF", "ON"},
            ),
            ocp_threshold_a=parse_protection_value_response(self.transport.query(f":OUTP:OCP:VAL? CH{channel}")),
            ocp_tripped=_enum_response(
                self.transport.query(f":OUTP:OCP:QUES? CH{channel}"),
                field="OCP trip state",
                allowed={"NO", "YES"},
            ),
        )

    def set_protection(
        self,
        channel: int,
        *,
        ovp_threshold_v: float | None = None,
        ovp_enabled: bool | None = None,
        ocp_threshold_a: float | None = None,
        ocp_enabled: bool | None = None,
        check_errors: bool = True,
    ) -> PowerProtectionStatus:
        self._validate_channel(channel)
        if ovp_threshold_v is not None and ovp_threshold_v < 0:
            raise DataError("OVP threshold must be >= 0")
        if ocp_threshold_a is not None and ocp_threshold_a <= 0:
            raise DataError("OCP threshold must be > 0")
        if ovp_threshold_v is not None:
            self.transport.write(f":OUTP:OVP:VAL CH{channel},{ovp_threshold_v:.12g}")
        if ovp_enabled is not None:
            self.transport.write(f":OUTP:OVP CH{channel},{'ON' if ovp_enabled else 'OFF'}")
        if ocp_threshold_a is not None:
            self.transport.write(f":OUTP:OCP:VAL CH{channel},{ocp_threshold_a:.12g}")
        if ocp_enabled is not None:
            self.transport.write(f":OUTP:OCP CH{channel},{'ON' if ocp_enabled else 'OFF'}")
        status = self.get_protection_status(channel)
        if check_errors:
            self.assert_no_errors()
        return status

    def set_voltage_current_limit(
        self,
        channel: int,
        voltage_v: float,
        current_limit_a: float,
        *,
        check_errors: bool = True,
        settle_ms_after_set: int = 0,
    ) -> PowerStatus:
        self._validate_channel(channel)
        if voltage_v < 0:
            raise DataError("voltage must be >= 0")
        if current_limit_a <= 0:
            raise DataError("current limit must be > 0")
        self.transport.write(f":APPL CH{channel},{voltage_v:.12g},{current_limit_a:.12g}")
        if settle_ms_after_set:
            time.sleep(settle_ms_after_set / 1000.0)
        status = self.get_status(channel)
        if check_errors:
            self.assert_no_errors()
        return status

    def set_output(
        self,
        channel: int,
        enabled: bool,
        *,
        check_errors: bool = True,
        settle_ms_after_output: int = 0,
    ) -> PowerStatus:
        self._validate_channel(channel)
        self.transport.write(f":OUTP CH{channel},{'ON' if enabled else 'OFF'}")
        if settle_ms_after_output:
            time.sleep(settle_ms_after_output / 1000.0)
        status = self.get_status(channel)
        if check_errors:
            self.assert_no_errors()
        return status

    def close(self) -> None:
        self.transport.close()
