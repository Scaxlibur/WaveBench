from __future__ import annotations

from dataclasses import dataclass
import math

from wavebench.errors import DataError
from wavebench.instruments.dmm import (
    DMM_FUNCTION_ALIASES as DMM_FUNCTION_ALIASES,
    normalize_dmm_function as normalize_dmm_function,
)
from wavebench.instruments.models import DmmMeasurementProfile, DmmReading

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


@dataclass
class DM3000Dmm:
    transport: object

    def idn(self) -> str:
        return self.transport.query("*IDN?")

    def function_status(self) -> str:
        raw = self.transport.query(":FUNCtion?").strip().strip('"')
        normalized = DMM_FUNCTION_QUERY_MAP.get(raw.upper())
        if normalized is None:
            supported = ", ".join(sorted(DMM_FUNCTION_QUERY_MAP))
            raise DataError(
                f"unexpected DMM function status {raw!r}; expected one of: {supported}"
            )
        return normalized

    def set_function(self, function: str) -> str:
        self.apply_function(function)
        return self.function_status()

    def measurement_profile(self) -> DmmMeasurementProfile:
        function = self.function_status()
        range_query = DMM_FUNCTION_RANGE_QUERIES.get(function)
        range_code = None
        auto_range = None
        if range_query is not None:
            raw_range = self.transport.query(range_query).strip()
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
            auto_range = range_code == 0
        impedance = None
        if function == "dcv":
            impedance = self.transport.query(":MEASure:VOLTage:DC:IMPedance?").strip().upper()
            if not impedance:
                raise DataError("unexpected empty DM3000 DCV impedance response")
        return DmmMeasurementProfile(
            function=function,
            range_code=range_code,
            auto_range=auto_range,
            impedance=impedance,
        )

    def apply_function(self, function: str) -> str:
        key = normalize_dmm_function(function)
        if key not in DMM_FUNCTION_SET_COMMANDS:
            supported = ", ".join(sorted(DMM_FUNCTION_SET_COMMANDS))
            raise DataError(f"unsupported DMM function {function!r}; supported: {supported}")
        self.transport.write(DMM_FUNCTION_SET_COMMANDS[key])
        return key

    def read(self, function: str = "dcv") -> DmmReading:
        key = normalize_dmm_function(function)
        if key not in DMM_FUNCTION_COMMANDS:
            supported = ", ".join(sorted(DMM_FUNCTION_COMMANDS))
            raise DataError(f"unsupported DMM function {function!r}; supported: {supported}")
        raw = self.transport.query(DMM_FUNCTION_COMMANDS[key])
        try:
            value = float(raw)
        except ValueError as exc:
            raise DataError(f"unexpected DM3000 reading for {key}: {raw!r}") from exc
        if not math.isfinite(value):
            raise DataError(f"non-finite DM3000 reading for {key}: {raw!r}")
        return DmmReading(function=key, value=value, unit=DMM_FUNCTION_UNITS[key], raw=raw)

    def close(self) -> None:
        self.transport.close()
