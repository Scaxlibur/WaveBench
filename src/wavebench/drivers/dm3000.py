from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from threading import RLock

from wavebench.errors import (
    DataError,
    InstrumentError,
    SessionHealthError,
    TransportIOError,
)
from wavebench.instruments.dmm import (
    DMM_FUNCTION_ALIASES as DMM_FUNCTION_ALIASES,
    normalize_dmm_function as normalize_dmm_function,
)
from wavebench.instruments.models import (
    DmmCalculationStatistics,
    DmmCalculationStatus,
    DmmDcvImpedanceConfiguration,
    DmmMeasurementProfile,
    DmmReading,
    DmmSystemInterfaceStatus,
    DmmTriggerStatus,
    DmmVoltageRangeConfiguration,
)
from wavebench.transport.contracts import ReplayPolicy

DMM_FUNCTION_COMMANDS = {
    "dcv": ":MEASure:VOLTage:DC?",
    "acv": ":MEASure:VOLTage:AC?",
    "dci": ":MEASure:CURRent:DC?",
    "aci": ":MEASure:CURRent:AC?",
    "res": ":MEASure:RESistance?",
    "fres": ":MEASure:FRESistance?",
    "freq": ":MEASure:FREQuency?",
    "period": ":MEASure:PERiod?",
    "continuity": ":MEASure:CONTinuity?",
    "diode": ":MEASure:DIODe?",
    "cap": ":MEASure:CAPacitance?",
}

DMM_FUNCTION_UNITS = {
    "dcv": "V",
    "acv": "V",
    "dci": "A",
    "aci": "A",
    "res": "ohm",
    "fres": "ohm",
    "freq": "Hz",
    "period": "s",
    "continuity": "ohm",
    "diode": "V",
    "cap": "F",
}

DMM_FUNCTION_RANGE_QUERIES = {
    "dcv": ":MEASure:VOLTage:DC:RANGe?",
    "acv": ":MEASure:VOLTage:AC:RANGe?",
    "dci": ":MEASure:CURRent:DC:RANGe?",
    "aci": ":MEASure:CURRent:AC:RANGe?",
    "res": ":MEASure:RESistance:RANGe?",
    "fres": ":MEASure:FRESistance:RANGe?",
    "freq": ":MEASure:FREQuency:RANGe?",
    "period": ":MEASure:PERiod:RANGe?",
    "cap": ":MEASure:CAPacitance:RANGe?",
}

DMM_FUNCTION_SET_COMMANDS = {
    "dcv": ":FUNCtion:VOLTage:DC",
    "acv": ":FUNCtion:VOLTage:AC",
    "dci": ":FUNCtion:CURRent:DC",
    "aci": ":FUNCtion:CURRent:AC",
    "res": ":FUNCtion:RESistance",
    "fres": ":FUNCtion:FRESistance",
    "freq": ":FUNCtion:FREQuency",
    "period": ":FUNCtion:PERiod",
    "continuity": ":FUNCtion:CONTinuity",
    "diode": ":FUNCtion:DIODe",
    "cap": ":FUNCtion:CAPacitance",
}

DMM_FUNCTION_QUERY_MAP = {
    # DM3000 manuals document long return symbols for :FUNCtion?, while DM3058
    # firmware can return the shorter scan/function symbols observed on LAN.
    "DCV": "dcv",
    "ACV": "acv",
    "DCI": "dci",
    "ACI": "aci",
    "RESISTANCE": "res",
    "RES": "res",
    "2WR": "res",
    "FRESISTANCE": "fres",
    "FRES": "fres",
    "4WR": "fres",
    "FREQUENCY": "freq",
    "FREQ": "freq",
    "PERIOD": "period",
    "PERI": "period",
    "CONTINUITY": "continuity",
    "CONT": "continuity",
    "DIODE": "diode",
    "CAPACITANCE": "cap",
    "CAP": "cap",
}

_CALCULATION_FUNCTIONS = {
    "NONE",
    "NULL",
    "DB",
    "DBM",
    "AVERAGE",
    "MIN",
    "MAX",
    "TOTAL",
    "LIMIT",
}
_CALCULATION_STATISTIC_QUERIES = {
    "average": ":CALCulate:STATistic:AVERage?",
    "min": ":CALCulate:STATistic:MIN?",
    "max": ":CALCulate:STATistic:MAX?",
}
_TIME_RESPONSE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)\s*(ns|us|ms|s)$",
    re.IGNORECASE,
)


def _finite_float(raw: str, field: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise DataError(f"unexpected DM3000 {field} response: {raw!r}") from exc
    if not math.isfinite(value):
        raise DataError(f"non-finite DM3000 {field} response: {raw!r}")
    return value


def _nonnegative_int(raw: str, field: str) -> int:
    value = _finite_float(raw, field)
    if value < 0 or not value.is_integer():
        raise DataError(f"unexpected DM3000 {field} response: {raw!r}")
    return int(value)


def _time_seconds(raw: str, field: str) -> float:
    match = _TIME_RESPONSE.fullmatch(raw.strip())
    if match is None:
        raise DataError(f"unexpected DM3000 {field} response: {raw!r}")
    value = _finite_float(match.group(1), field)
    factors = {"ns": 1.0e-9, "us": 1.0e-6, "ms": 1.0e-3, "s": 1.0}
    seconds = value * factors[match.group(2).lower()]
    if not math.isfinite(seconds) or seconds <= 0:
        raise DataError(f"unexpected DM3000 {field} response: {raw!r}")
    return seconds


def _on_off(raw: str, field: str) -> bool:
    normalized = raw.strip().upper()
    if normalized == "ON":
        return True
    if normalized == "OFF":
        return False
    raise DataError(f"unexpected DM3000 {field} response: {raw!r}")


def _zero_one(raw: str, field: str) -> bool:
    normalized = raw.strip()
    if normalized == "1":
        return True
    if normalized == "0":
        return False
    raise DataError(f"unexpected DM3000 {field} response: {raw!r}")


def _enum_response(raw: str, field: str, allowed: set[str]) -> str:
    normalized = raw.strip().strip('"').upper()
    if normalized not in allowed:
        raise DataError(f"unexpected DM3000 {field} response: {raw!r}")
    return normalized


def _installed_status(raw: str, field: str) -> bool:
    normalized = raw.strip().upper()
    if normalized == "INSTALLED":
        return True
    if normalized == "NONE":
        return False
    raise DataError(f"unexpected DM3000 {field} response: {raw!r}")


@dataclass
class DM3000Dmm:
    transport: object
    _io_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _configuration_writes_blocked: bool = field(default=False, init=False, repr=False)

    def idn(self) -> str:
        with self._io_lock:
            return self.transport.query("*IDN?", replay=ReplayPolicy.NO_REPLAY)

    def function_status(self) -> str:
        with self._io_lock:
            raw = self.transport.query(":FUNCtion?", replay=ReplayPolicy.NO_REPLAY).strip().strip('"')
            normalized = DMM_FUNCTION_QUERY_MAP.get(raw.upper())
            if normalized is None:
                supported = ", ".join(sorted(DMM_FUNCTION_QUERY_MAP))
                raise DataError(
                    f"unexpected DMM function status {raw!r}; expected one of: {supported}"
                )
            return normalized

    def set_function(self, function: str) -> str:
        with self._io_lock:
            self.apply_function(function)
            return self.function_status()

    def measurement_profile(self) -> DmmMeasurementProfile:
        with self._io_lock:
            function = self.function_status()
            range_query = DMM_FUNCTION_RANGE_QUERIES.get(function)
            range_code = None
            auto_range = None
            if range_query is not None:
                raw_range = self.transport.query(range_query, replay=ReplayPolicy.NO_REPLAY).strip()
                try:
                    range_code = int(raw_range)
                except ValueError as exc:
                    raise DataError(
                        f"unexpected DM3000 range code for {function}: {raw_range!r}"
                    ) from exc
                if range_code < 0:
                    raise DataError(
                        f"unexpected DM3000 range code for {function}: {raw_range!r}"
                    )
            impedance = None
            if function == "dcv":
                impedance = self.transport.query(
                    ":MEASure:VOLTage:DC:IMPedance?", replay=ReplayPolicy.NO_REPLAY
                ).strip().upper()
                if not impedance:
                    raise DataError("unexpected empty DM3000 DCV impedance response")
            return DmmMeasurementProfile(
                function=function,
                range_code=range_code,
                auto_range=auto_range,
                impedance=impedance,
            )

    def trigger_status(self) -> DmmTriggerStatus:
        with self._io_lock:
            source = self.transport.query(":TRIGger:SOURce?", replay=ReplayPolicy.NO_REPLAY).strip().upper()
            if not source:
                raise DataError("unexpected empty DM3000 trigger source response")
            auto_interval_s = _time_seconds(
                self.transport.query(":TRIGger:AUTO:INTerval?", replay=ReplayPolicy.NO_REPLAY),
                "trigger auto interval",
            )
            auto_hold = _on_off(
                self.transport.query(":TRIGger:AUTO:HOLD?", replay=ReplayPolicy.NO_REPLAY),
                "trigger auto hold",
            )
            auto_hold_sensitivity = _nonnegative_int(
                self.transport.query(":TRIGger:AUTO:HOLD:SENSitivity?", replay=ReplayPolicy.NO_REPLAY),
                "trigger auto hold sensitivity",
            )
            single_count = _nonnegative_int(
                self.transport.query(":TRIGger:SINGle?", replay=ReplayPolicy.NO_REPLAY),
                "trigger single count",
            )
            external_slope = self.transport.query(":TRIGger:EXT?", replay=ReplayPolicy.NO_REPLAY).strip().upper()
            vmc_polarity = self.transport.query(":TRIGger:VMComplete:POLar?", replay=ReplayPolicy.NO_REPLAY).strip().upper()
            if not external_slope or not vmc_polarity:
                raise DataError("unexpected empty DM3000 trigger edge or VMC polarity response")
            vmc_pulse_width_s = _time_seconds(
                self.transport.query(":TRIGger:VMComplete:PULSewidth?", replay=ReplayPolicy.NO_REPLAY),
                "trigger VMC pulse width",
            )
            try:
                return DmmTriggerStatus(
                    source=source,
                    auto_interval_s=auto_interval_s,
                    auto_hold=auto_hold,
                    auto_hold_sensitivity=auto_hold_sensitivity,
                    single_count=single_count,
                    external_slope=external_slope,
                    vmc_polarity=vmc_polarity,
                    vmc_pulse_width_s=vmc_pulse_width_s,
                )
            except ValueError as exc:
                raise DataError(f"invalid DM3000 trigger status: {exc}") from exc

    def _calculation_function(self) -> str:
        raw = self.transport.query(":CALCulate:FUNCtion?", replay=ReplayPolicy.NO_REPLAY).strip().strip('"').upper()
        if raw not in _CALCULATION_FUNCTIONS:
            raise DataError(f"unexpected DM3000 calculation function response: {raw!r}")
        return raw.lower()

    def calculation_status(self) -> DmmCalculationStatus:
        with self._io_lock:
            function = self._calculation_function()
            statistic_count = _nonnegative_int(
                self.transport.query(":CALCulate:STATistic:COUNt?", replay=ReplayPolicy.NO_REPLAY),
                "calculation statistic count",
            )
            db_reference = _finite_float(
                self.transport.query(":CALCulate:DB:REFerence?", replay=ReplayPolicy.NO_REPLAY),
                "calculation dB reference",
            )
            dbm_reference_ohm = _finite_float(
                self.transport.query(":CALCulate:DBM:REFerence?", replay=ReplayPolicy.NO_REPLAY),
                "calculation dBm reference",
            )
            if dbm_reference_ohm <= 0:
                raise DataError("DM3000 calculation dBm reference must be positive")
            return DmmCalculationStatus(
                function=function,
                statistic_count=statistic_count,
                db_reference=db_reference,
                dbm_reference_ohm=dbm_reference_ohm,
            )

    def calculation_statistics(self, expected_function: str) -> DmmCalculationStatistics:
        expected = expected_function.strip().lower()
        if expected not in _CALCULATION_STATISTIC_QUERIES:
            raise DataError("DM3000 calculation statistic must be average, min, or max")
        with self._io_lock:
            active = self._calculation_function()
            if active != expected:
                raise InstrumentError(
                    f"DM3000 calculation statistics requires active function {expected}; "
                    f"current function is {active}"
                )
            value = _finite_float(
                self.transport.query(_CALCULATION_STATISTIC_QUERIES[expected], replay=ReplayPolicy.NO_REPLAY),
                f"calculation {expected} statistic",
            )
            count = _nonnegative_int(
                self.transport.query(":CALCulate:STATistic:COUNt?", replay=ReplayPolicy.NO_REPLAY),
                "calculation statistic count",
            )
            return DmmCalculationStatistics(function=expected, value=value, count=count)

    def system_interface_status(self) -> DmmSystemInterfaceStatus:
        with self._io_lock:
            beeper_enabled = _zero_one(
                self.transport.query(":SYSTem:BEEPer:STATe?", replay=ReplayPolicy.NO_REPLAY),
                "beeper state",
            )
            language = _enum_response(
                self.transport.query(":SYSTem:LANGuage?", replay=ReplayPolicy.NO_REPLAY),
                "language",
                {"CHINESE", "ENGLISH"},
            )
            decimal_format = _enum_response(
                self.transport.query(":SYSTem:FORMat:DECimal?", replay=ReplayPolicy.NO_REPLAY),
                "decimal format",
                {"COMMA", "DOT"},
            )
            separator_format = _enum_response(
                self.transport.query(":SYSTem:FORMat:SEParate?", replay=ReplayPolicy.NO_REPLAY),
                "separator format",
                {"ON", "NONE", "SPACE"},
            )
            display_brightness = _nonnegative_int(
                self.transport.query(":SYSTem:DISPlay:BRIGht?", replay=ReplayPolicy.NO_REPLAY),
                "display brightness",
            )
            scan_board_installed = _installed_status(
                self.transport.query(":SYSTem:SCANserial?", replay=ReplayPolicy.NO_REPLAY),
                "scan board status",
            )
            lan_interface_installed = _installed_status(
                self.transport.query(":SYSTem:LANserial?", replay=ReplayPolicy.NO_REPLAY),
                "LAN interface status",
            )
            dhcp_enabled = _on_off(
                self.transport.query(":UTILity:INTerface:LAN:DHCP?", replay=ReplayPolicy.NO_REPLAY),
                "LAN DHCP state",
            )
            gpib_address = _nonnegative_int(
                self.transport.query(":UTILity:INTerface:GPIB:ADDRess?", replay=ReplayPolicy.NO_REPLAY),
                "GPIB address",
            )
            rs232_baud = _nonnegative_int(
                self.transport.query(":UTILity:INTerface:RS232:BAUD?", replay=ReplayPolicy.NO_REPLAY),
                "RS-232 baud rate",
            )
            rs232_parity = _enum_response(
                self.transport.query(":UTILity:INTerface:RS232:PARity?", replay=ReplayPolicy.NO_REPLAY),
                "RS-232 parity",
                {"NONE8BITS", "ODD7BITS", "EVEN7BITS"},
            )
            try:
                return DmmSystemInterfaceStatus(
                    beeper_enabled=beeper_enabled,
                    language=language,
                    decimal_format=decimal_format,
                    separator_format=separator_format,
                    display_brightness=display_brightness,
                    scan_board_installed=scan_board_installed,
                    lan_interface_installed=lan_interface_installed,
                    dhcp_enabled=dhcp_enabled,
                    gpib_address=gpib_address,
                    rs232_baud=rs232_baud,
                    rs232_parity=rs232_parity,
                )
            except ValueError as exc:
                raise DataError(f"invalid DM3000 system/interface status: {exc}") from exc

    @property
    def configuration_writes_blocked(self) -> bool:
        with self._io_lock:
            return self._configuration_writes_blocked

    def _range_code(self, function: str) -> int:
        raw = self.transport.query(DMM_FUNCTION_RANGE_QUERIES[function], replay=ReplayPolicy.NO_REPLAY).strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise DataError(f"unexpected DM3000 range code for {function}: {raw!r}") from exc
        if not 0 <= value <= 4:
            raise DataError(f"unexpected DM3000 range code for {function}: {raw!r}")
        return value

    def _dcv_impedance(self) -> str:
        raw = self.transport.query(":MEASure:VOLTage:DC:IMPedance?", replay=ReplayPolicy.NO_REPLAY).strip().upper()
        if raw not in {"10M", "10G"}:
            raise DataError(f"unexpected DM3000 DCV impedance response: {raw!r}")
        return raw

    def _require_configuration_writes(self) -> None:
        if self._configuration_writes_blocked:
            raise InstrumentError(
                "DM3000 configuration writes are blocked after an earlier ambiguous transaction"
            )

    def set_voltage_range(
        self,
        function: str,
        range_code: int,
    ) -> DmmVoltageRangeConfiguration:
        key = normalize_dmm_function(function)
        if key not in {"dcv", "acv"}:
            raise DataError("DM3000 voltage range function must be dcv or acv")
        if isinstance(range_code, bool) or not isinstance(range_code, int) or not 0 <= range_code <= 4:
            raise DataError("DM3000 voltage range code must be an integer from 0 to 4")
        with self._io_lock:
            self._require_configuration_writes()
            active = self.function_status()
            if active != key:
                raise InstrumentError(
                    f"DM3000 voltage range requires active function {key}; current function is {active}"
                )
            previous = self._range_code(key)
            if previous == range_code:
                return DmmVoltageRangeConfiguration(key, previous, range_code)
            if key == "dcv" and range_code in {3, 4} and self._dcv_impedance() == "10G":
                raise InstrumentError(
                    "DM3000 DCV range codes 3 and 4 require 10M impedance; "
                    "set impedance explicitly before changing range"
                )
            command = (
                ":MEASure:VOLTage:DC" if key == "dcv" else ":MEASure:VOLTage:AC"
            )
            write_returned = False
            try:
                self.transport.write(f"{command} {range_code}")
                write_returned = True
                if self._range_code(key) != range_code:
                    raise DataError("DM3000 voltage range readback mismatch")
            except (TransportIOError, SessionHealthError):
                # A session-gated or structured exchange failure must not
                # trigger another write on an uncertain/poisoned connection.
                raise
            except Exception as exc:
                try:
                    self.transport.write(f"{command} {previous}")
                    if self._range_code(key) != previous:
                        raise DataError("DM3000 voltage range restore mismatch")
                except (TransportIOError, SessionHealthError):
                    raise
                except Exception as restore_exc:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DM3000 voltage range transaction failed and restoration is ambiguous; "
                        "configuration writes are blocked"
                    ) from restore_exc
                self._configuration_writes_blocked = True
                if not write_returned:
                    raise InstrumentError(
                        "DM3000 voltage range write outcome is ambiguous; the original range was "
                        "restored but configuration writes are blocked"
                    ) from exc
                raise InstrumentError(
                    "DM3000 voltage range change failed; the original range was restored, the "
                    "measurement mode may remain manual, and configuration writes are blocked"
                ) from exc
            return DmmVoltageRangeConfiguration(key, previous, range_code)

    def set_dcv_impedance(self, impedance: str) -> DmmDcvImpedanceConfiguration:
        target = impedance.strip().upper()
        if target not in {"10M", "10G"}:
            raise DataError("DM3000 DCV impedance must be 10M or 10G")
        with self._io_lock:
            self._require_configuration_writes()
            active = self.function_status()
            if active != "dcv":
                raise InstrumentError(
                    f"DM3000 DCV impedance requires active function dcv; current function is {active}"
                )
            range_code = self._range_code("dcv")
            if target == "10G" and range_code not in {0, 1, 2}:
                raise InstrumentError(
                    "DM3000 DCV 10G impedance requires range code 0, 1, or 2"
                )
            previous = self._dcv_impedance()
            if previous == target:
                return DmmDcvImpedanceConfiguration(previous, target, range_code)
            write_returned = False
            try:
                self.transport.write(f":MEASure:VOLTage:DC:IMPedance {target}")
                write_returned = True
                if self._dcv_impedance() != target:
                    raise DataError("DM3000 DCV impedance readback mismatch")
            except (TransportIOError, SessionHealthError):
                raise
            except Exception as exc:
                try:
                    self.transport.write(f":MEASure:VOLTage:DC:IMPedance {previous}")
                    if self._dcv_impedance() != previous:
                        raise DataError("DM3000 DCV impedance restore mismatch")
                except (TransportIOError, SessionHealthError):
                    raise
                except Exception as restore_exc:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DM3000 DCV impedance transaction failed and restoration is ambiguous; "
                        "configuration writes are blocked"
                    ) from restore_exc
                if not write_returned:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DM3000 DCV impedance write outcome is ambiguous; the original value was "
                        "restored but configuration writes are blocked"
                    ) from exc
                raise InstrumentError(
                    "DM3000 DCV impedance change failed; the original value was restored"
                ) from exc
            return DmmDcvImpedanceConfiguration(previous, target, range_code)

    def apply_function(self, function: str) -> str:
        key = normalize_dmm_function(function)
        if key not in DMM_FUNCTION_SET_COMMANDS:
            supported = ", ".join(sorted(DMM_FUNCTION_SET_COMMANDS))
            raise DataError(f"unsupported DMM function {function!r}; supported: {supported}")
        with self._io_lock:
            self._require_configuration_writes()
            self.transport.write(DMM_FUNCTION_SET_COMMANDS[key])
            return key

    def read(self, function: str = "dcv") -> DmmReading:
        key = normalize_dmm_function(function)
        if key not in DMM_FUNCTION_COMMANDS:
            supported = ", ".join(sorted(DMM_FUNCTION_COMMANDS))
            raise DataError(f"unsupported DMM function {function!r}; supported: {supported}")
        with self._io_lock:
            raw = self.transport.query(DMM_FUNCTION_COMMANDS[key], replay=ReplayPolicy.NO_REPLAY)
            try:
                value = float(raw)
            except ValueError as exc:
                raise DataError(f"unexpected DM3000 reading for {key}: {raw!r}") from exc
            if not math.isfinite(value):
                raise DataError(f"non-finite DM3000 reading for {key}: {raw!r}")
            return DmmReading(function=key, value=value, unit=DMM_FUNCTION_UNITS[key], raw=raw)

    def close(self) -> None:
        with self._io_lock:
            self.transport.close()
