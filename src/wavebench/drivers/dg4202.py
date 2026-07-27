from __future__ import annotations

from dataclasses import dataclass, field
from math import isclose, isfinite
from threading import RLock

from wavebench.errors import DataError, InstrumentError
from wavebench.instruments.dg4000 import DG4000DacBlock
from wavebench.instruments.models import ArbitraryQueryProbeResult, SourceStatus


ARBITRARY_QUERY_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("current_function", ":SOUR{channel}:FUNC?"),
    ("user_function", ":SOUR{channel}:FUNC:USER?"),
    ("arb_function", ":SOUR{channel}:FUNC:ARB?"),
    ("arb_state", ":SOUR{channel}:ARB?"),
    ("arb_sample_rate", ":SOUR{channel}:ARB:SRAT?"),
    ("arb_frequency", ":SOUR{channel}:ARB:FREQ?"),
    ("source_data_catalog", ":SOUR{channel}:DATA:CAT?"),
    ("source_data", ":SOUR{channel}:DATA?"),
    ("global_data_catalog", ":DATA:CAT?"),
)

_KNOWN_MODELS = frozenset({"DG4062", "DG4102", "DG4162", "DG4202"})
_WRITE_ACCEPTED_MODELS = frozenset({"DG4202"})
_RESTORABLE_BASIC_FUNCTIONS = frozenset({"SIN", "SQU", "RAMP", "PULS", "NOIS", "DC"})
_FUNCTION_ALIASES = {
    "SIN": "SIN",
    "SINE": "SIN",
    "SQU": "SQU",
    "SQUARE": "SQU",
    "RAMP": "RAMP",
    "TRI": "RAMP",
    "TRIANGLE": "RAMP",
    "PULS": "PULS",
    "PULSE": "PULS",
    "NOIS": "NOIS",
    "NOISE": "NOIS",
    "DC": "DC",
    "USER": "USER",
    "ARB": "USER",
    "HARM": "HARM",
}


def _validate_channel(channel: int) -> None:
    if channel not in (1, 2):
        raise DataError("DG4000 channel must be 1 or 2")


def _finite_float(value: object, *, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise DataError(f"{field_name} must be a finite number") from exc
    if not isfinite(parsed):
        raise DataError(f"{field_name} must be a finite number")
    return parsed


def _normalize_enum(
    value: object,
    *,
    field_name: str,
    aliases: dict[str, str],
) -> str:
    normalized = str(value).strip().strip('"').upper()
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise DataError(f"unexpected {field_name} response: {value!r}") from exc


def _parse_identity(response: str) -> tuple[str, str, str, str]:
    parts = tuple(item.strip() for item in response.split(","))
    if len(parts) != 4 or any(not item for item in parts):
        raise DataError("unexpected DG4000 *IDN? response")
    manufacturer, model, serial_number, firmware = parts
    if manufacturer.upper() != "RIGOL TECHNOLOGIES":
        raise DataError(f"unexpected DG4000 manufacturer: {manufacturer!r}")
    normalized_model = model.upper()
    if normalized_model not in _KNOWN_MODELS:
        raise DataError(f"unsupported DG4000 model: {model!r}")
    return manufacturer, normalized_model, serial_number, firmware


def _validate_apply_response(response: str) -> None:
    normalized = response.strip().strip('"')
    parts = tuple(item.strip() for item in normalized.split(","))
    if len(parts) != 5:
        raise DataError(f"unexpected DG4000 APPLy? response: {response!r}")
    _normalize_enum(
        parts[0],
        field_name="application function",
        aliases=_FUNCTION_ALIASES,
    )
    for field_name, value in zip(
        ("application frequency", "application amplitude", "application offset", "application phase"),
        parts[1:],
        strict=True,
    ):
        _finite_float(value, field_name=field_name)


def _validate_dac14_block(block: DG4000DacBlock) -> None:
    if not isinstance(block, DG4000DacBlock):
        raise DataError("arbitrary upload requires a validated DG4000DacBlock")
    if getattr(block.byte_order, "value", block.byte_order) != "little":
        raise DataError("DG4000 DAC14 upload requires little-endian samples")
    if block.points < 2 or block.points > 16384:
        raise DataError("DG4000 DAC14 upload requires 2..16384 points")
    if block.data_bytes != block.points * 2:
        raise DataError("DG4000 DAC14 block byte count does not match its point count")

    prefix = b":DATA:DAC VOLATILE,"
    command = block.command
    if not isinstance(command, bytes) or not command.startswith(prefix + b"#"):
        raise DataError("unexpected DG4000 DAC14 command prefix")
    encoded = command[len(prefix) :]
    if len(encoded) < 3 or encoded[1:2] < b"1" or encoded[1:2] > b"9":
        raise DataError("unexpected DG4000 DAC14 binary block header")
    length_digits = int(encoded[1:2])
    header_end = 2 + length_digits
    if len(encoded) < header_end or not encoded[2:header_end].isdigit():
        raise DataError("unexpected DG4000 DAC14 binary block length")
    declared_bytes = int(encoded[2:header_end])
    payload = encoded[header_end:]
    if declared_bytes != block.data_bytes or len(payload) != block.data_bytes:
        raise DataError("DG4000 DAC14 binary block payload length mismatch")
    if any(
        int.from_bytes(payload[index : index + 2], "little") > 16383
        for index in range(0, len(payload), 2)
    ):
        raise DataError("DG4000 DAC14 samples must be within 0..16383")


class _AmbiguousWriteError(InstrumentError):
    pass


@dataclass
class DG4202Source:
    transport: object
    check_errors_after_ops: bool = True
    _io_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _identity: tuple[str, str, str, str] | None = field(default=None, init=False, repr=False)
    _configuration_writes_blocked: bool = field(default=False, init=False, repr=False)

    def _query_finite_float(self, command: str, *, field_name: str) -> float:
        return _finite_float(self.transport.query(command), field_name=field_name)

    def _ensure_identity(self, *, write: bool = False) -> tuple[str, str, str, str]:
        if self._identity is None:
            self._identity = _parse_identity(self.transport.query("*IDN?"))
        if write and self._identity[1] not in _WRITE_ACCEPTED_MODELS:
            raise DataError(
                f"DG4000 configuration writes are not accepted on model {self._identity[1]}"
            )
        return self._identity

    def _check_errors_enabled(self, requested: bool | None) -> bool:
        return self.check_errors_after_ops if requested is None else requested

    def _ensure_configuration_write_allowed(self) -> None:
        if self._configuration_writes_blocked:
            raise InstrumentError(
                "DG4000 configuration writes are blocked after an ambiguous or "
                "unrecoverable transaction; reopen the instrument session and verify state"
            )

    def _write(self, command: str) -> None:
        try:
            self.transport.write(command)
        except Exception as exc:
            raise _AmbiguousWriteError(
                f"DG4000 write result is unknown for {command!r}: {exc}"
            ) from exc

    def _write_bytes(self, command: bytes) -> None:
        try:
            writer = getattr(self.transport, "write_bytes", None)
            if not callable(writer):
                raise InstrumentError(
                    "transport does not support binary arbitrary waveform upload"
                )
            writer(command)
        except Exception as exc:
            raise _AmbiguousWriteError(
                "DG4000 binary write result is unknown; the volatile waveform may "
                f"have changed: {exc}"
            ) from exc

    def _query_output(self, channel: int) -> str:
        return _normalize_enum(
            self.transport.query(f":OUTP{channel}?"),
            field_name="output state",
            aliases={"0": "OFF", "OFF": "OFF", "1": "ON", "ON": "ON"},
        )

    def _query_function(self, channel: int) -> str:
        return _normalize_enum(
            self.transport.query(f":SOUR{channel}:FUNC?"),
            field_name="function",
            aliases=_FUNCTION_ALIASES,
        )

    def _query_amplitude_unit(self, channel: int) -> str:
        return _normalize_enum(
            self.transport.query(f":SOUR{channel}:VOLT:UNIT?"),
            field_name="amplitude unit",
            aliases={"VPP": "VPP", "VRMS": "VRMS", "DBM": "DBM"},
        )

    def _query_frequency_mode(self, channel: int) -> str:
        return _normalize_enum(
            self.transport.query(f":SOUR{channel}:FREQ:MODE?"),
            field_name="frequency mode",
            aliases={"FIX": "FIX", "SWE": "SWE"},
        )

    @staticmethod
    def _numeric_matches(actual: float | None, expected: float) -> bool:
        return actual is not None and isclose(
            actual,
            expected,
            rel_tol=1.0e-6,
            abs_tol=1.0e-6,
        )

    def _force_output_off(self, channel: int) -> None:
        self._write(f":OUTP{channel} OFF")
        if self._query_output(channel) != "OFF":
            raise InstrumentError("DG4000 output did not converge to OFF")

    def _snapshot_basic_status(self, channel: int) -> SourceStatus:
        snapshot = self.get_status(channel)
        if snapshot.function not in _RESTORABLE_BASIC_FUNCTIONS:
            raise DataError(
                "DG4000 fixed-wave transactions require a restorable basic function "
                "snapshot"
            )
        return snapshot

    def _restore_basic_status(self, snapshot: SourceStatus) -> SourceStatus:
        channel = snapshot.channel
        self._force_output_off(channel)

        self._write(f":SOUR{channel}:FUNC {snapshot.function}")
        if self._query_function(channel) != snapshot.function:
            raise InstrumentError("DG4000 function restore readback mismatch")

        self._write(f":SOUR{channel}:FREQ:MODE FIX")
        if self._query_frequency_mode(channel) != "FIX":
            raise InstrumentError("DG4000 FIX-mode recovery readback mismatch")

        self._write(f":SOUR{channel}:FREQ {snapshot.frequency_hz:.12g}")
        restored_frequency = self._query_finite_float(
            f":SOUR{channel}:FREQ?", field_name="frequency"
        )
        if not self._numeric_matches(restored_frequency, snapshot.frequency_hz):
            raise InstrumentError("DG4000 frequency restore readback mismatch")

        self._write(f":SOUR{channel}:FREQ:MODE {snapshot.frequency_mode}")
        if self._query_frequency_mode(channel) != snapshot.frequency_mode:
            raise InstrumentError("DG4000 frequency-mode restore readback mismatch")

        self._write(f":SOUR{channel}:VOLT:UNIT {snapshot.amplitude_unit}")
        if self._query_amplitude_unit(channel) != snapshot.amplitude_unit:
            raise InstrumentError("DG4000 amplitude-unit restore readback mismatch")

        self._write(f":SOUR{channel}:VOLT {snapshot.amplitude:.12g}")
        restored_amplitude = self._query_finite_float(
            f":SOUR{channel}:VOLT?", field_name="amplitude"
        )
        if not self._numeric_matches(restored_amplitude, snapshot.amplitude):
            raise InstrumentError("DG4000 amplitude restore readback mismatch")

        self._write(f":SOUR{channel}:VOLT:OFFS {snapshot.offset_v:.12g}")
        restored_offset = self._query_finite_float(
            f":SOUR{channel}:VOLT:OFFS?", field_name="offset"
        )
        if not self._numeric_matches(restored_offset, snapshot.offset_v):
            raise InstrumentError("DG4000 offset restore readback mismatch")

        if snapshot.square_duty_cycle_percent is not None:
            self._write(
                f":SOUR{channel}:FUNC:SQU:DCYC "
                f"{snapshot.square_duty_cycle_percent:.12g}"
            )
            restored_duty = self._query_finite_float(
                f":SOUR{channel}:FUNC:SQU:DCYC?", field_name="square duty cycle"
            )
            if not self._numeric_matches(
                restored_duty, snapshot.square_duty_cycle_percent
            ):
                raise InstrumentError("DG4000 duty-cycle restore readback mismatch")

        if snapshot.output == "ON":
            self._write(f":OUTP{channel} ON")
            if self._query_output(channel) != "ON":
                raise InstrumentError("DG4000 output restore readback mismatch")

        restored = self.get_status(channel)
        if (
            restored.output != snapshot.output
            or restored.function != snapshot.function
            or restored.frequency_mode != snapshot.frequency_mode
            or restored.sweep_enabled != snapshot.sweep_enabled
            or restored.amplitude_unit != snapshot.amplitude_unit
            or not self._numeric_matches(restored.frequency_hz, snapshot.frequency_hz)
            or not self._numeric_matches(restored.amplitude, snapshot.amplitude)
            or not self._numeric_matches(restored.offset_v, snapshot.offset_v)
            or (
                snapshot.square_duty_cycle_percent is not None
                and not self._numeric_matches(
                    restored.square_duty_cycle_percent,
                    snapshot.square_duty_cycle_percent,
                )
            )
        ):
            raise InstrumentError("DG4000 basic-state restore verification failed")
        return restored

    def _recover_configuration_failure(
        self,
        *,
        snapshot: SourceStatus,
        original_error: Exception,
    ) -> None:
        ambiguous = isinstance(original_error, _AmbiguousWriteError)
        try:
            self._restore_basic_status(snapshot)
        except Exception as recovery_error:
            self._configuration_writes_blocked = True
            raise InstrumentError(
                "DG4000 transaction failed and basic-state recovery could not be verified; "
                "configuration writes are blocked"
            ) from recovery_error
        if ambiguous:
            self._configuration_writes_blocked = True
            raise InstrumentError(
                "DG4000 transaction had an ambiguous write; basic state was restored but "
                "configuration writes are blocked until the session is reopened"
            ) from original_error

    def _recover_arbitrary_upload_failure(
        self,
        *,
        snapshot: SourceStatus,
        original_error: Exception,
    ) -> None:
        self._configuration_writes_blocked = True
        try:
            self._restore_basic_status(snapshot)
        except Exception as recovery_error:
            raise InstrumentError(
                "DG4000 arbitrary upload failed after the volatile waveform write was "
                "attempted, basic-state recovery could not be verified, and configuration "
                "writes are blocked"
            ) from recovery_error
        raise InstrumentError(
            "DG4000 arbitrary upload failed after the volatile waveform write was attempted; "
            "basic state was restored, but the previous volatile waveform cannot be restored "
            "and configuration writes are blocked until the session is reopened"
        ) from original_error

    def _finish_transaction(
        self,
        *,
        channel: int,
        check_errors: bool | None,
    ) -> SourceStatus:
        status = self.get_status(channel)
        if self._check_errors_enabled(check_errors):
            self.assert_no_errors()
        return status

    def idn(self) -> str:
        with self._io_lock:
            response = self.transport.query("*IDN?")
            self._identity = _parse_identity(response)
            return response

    def errors(self, limit: int = 8) -> list[str]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise DataError("error query limit must be a positive integer")
        with self._io_lock:
            errors: list[str] = []
            for _ in range(limit):
                response = self.transport.query("SYST:ERR?").strip()
                if not response:
                    raise DataError("empty DG4000 error-queue response")
                errors.append(response)
                if response.startswith("0") or "No error" in response:
                    break
            return errors

    def assert_no_errors(self) -> None:
        with self._io_lock:
            active = [
                item
                for item in self.errors()
                if not (item.startswith("0") or "No error" in item)
            ]
            if active:
                raise InstrumentError("instrument error queue is not empty: " + "; ".join(active))

    def get_status(self, channel: int) -> SourceStatus:
        _validate_channel(channel)
        with self._io_lock:
            self._ensure_identity()
            output = self._query_output(channel)
            function = self._query_function(channel)
            frequency_hz = self._query_finite_float(
                f":SOUR{channel}:FREQ?", field_name="frequency"
            )
            amplitude = self._query_finite_float(
                f":SOUR{channel}:VOLT?", field_name="amplitude"
            )
            amplitude_unit = self._query_amplitude_unit(channel)
            offset_v = self._query_finite_float(
                f":SOUR{channel}:VOLT:OFFS?", field_name="offset"
            )
            phase_deg = self._query_finite_float(
                f":SOUR{channel}:PHAS?", field_name="phase"
            )
            frequency_mode = self._query_frequency_mode(channel)
            sweep_enabled = _normalize_enum(
                self.transport.query(f":SOUR{channel}:SWE:STAT?"),
                field_name="sweep state",
                aliases={"0": "OFF", "OFF": "OFF", "1": "ON", "ON": "ON"},
            )
            apply_raw = self.transport.query(f":SOUR{channel}:APPL?").strip()
            _validate_apply_response(apply_raw)
            duty = self._query_finite_float(
                f":SOUR{channel}:FUNC:SQU:DCYC?", field_name="square duty cycle"
            )
            if duty <= 0 or duty >= 100:
                raise DataError("square duty cycle response must be > 0 and < 100")
            return SourceStatus(
                channel=channel,
                output=output,
                function=function,
                frequency_hz=frequency_hz,
                amplitude=amplitude,
                amplitude_unit=amplitude_unit,
                offset_v=offset_v,
                phase_deg=phase_deg,
                frequency_mode=frequency_mode,
                sweep_enabled=sweep_enabled,
                apply_raw=apply_raw,
                square_duty_cycle_percent=duty,
            )

    def set_frequency(
        self,
        channel: int,
        value_hz: float,
        *,
        ensure_fix_mode: bool = True,
        check_errors: bool | None = None,
    ) -> SourceStatus:
        _validate_channel(channel)
        value_hz = _finite_float(value_hz, field_name="frequency")
        if value_hz <= 0:
            raise DataError("frequency must be > 0")
        with self._io_lock:
            self._ensure_identity(write=True)
            self._ensure_configuration_write_allowed()
            snapshot = self._snapshot_basic_status(channel)
            if snapshot.frequency_mode != "FIX" and not ensure_fix_mode:
                raise DataError(
                    "DG4000 frequency writes require FIX mode when automatic mode "
                    "selection is disabled"
                )
            try:
                if ensure_fix_mode and snapshot.frequency_mode != "FIX":
                    self._write(f":SOUR{channel}:FREQ:MODE FIX")
                    if self._query_frequency_mode(channel) != "FIX":
                        raise InstrumentError(
                            "DG4000 frequency-mode write readback mismatch"
                        )
                self._write(f":SOUR{channel}:FREQ {value_hz:.12g}")
                actual = self._query_finite_float(
                    f":SOUR{channel}:FREQ?", field_name="frequency"
                )
                if not self._numeric_matches(actual, value_hz):
                    raise InstrumentError("DG4000 frequency write readback mismatch")
                return self._finish_transaction(
                    channel=channel,
                    check_errors=check_errors,
                )
            except Exception as exc:
                self._recover_configuration_failure(
                    snapshot=snapshot,
                    original_error=exc,
                )
                raise

    def set_output(
        self,
        channel: int,
        enabled: bool,
        *,
        check_errors: bool | None = None,
    ) -> SourceStatus:
        _validate_channel(channel)
        if not isinstance(enabled, bool):
            raise DataError("output enabled must be a boolean")
        with self._io_lock:
            self._ensure_identity(write=True)
            if enabled:
                self._ensure_configuration_write_allowed()
                self._query_output(channel)
                if self._check_errors_enabled(check_errors):
                    self.assert_no_errors()
            target = "ON" if enabled else "OFF"
            try:
                self._write(f":OUTP{channel} {target}")
                if self._query_output(channel) != target:
                    raise InstrumentError("DG4000 output write readback mismatch")
                return self._finish_transaction(
                    channel=channel,
                    check_errors=check_errors,
                )
            except Exception as exc:
                ambiguous = isinstance(exc, _AmbiguousWriteError)
                try:
                    self._force_output_off(channel)
                except Exception as recovery_error:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DG4000 output transaction failed and OFF recovery could not be "
                        "verified; configuration writes are blocked"
                    ) from recovery_error
                if ambiguous:
                    self._configuration_writes_blocked = True
                    raise InstrumentError(
                        "DG4000 output write was ambiguous; output is confirmed OFF and "
                        "configuration writes are blocked until the session is reopened"
                    ) from exc
                raise

    def set_function(
        self,
        channel: int,
        function: str,
        *,
        check_errors: bool | None = None,
    ) -> SourceStatus:
        _validate_channel(channel)
        normalized = function.strip().upper()
        aliases = {
            key: value
            for key, value in _FUNCTION_ALIASES.items()
            if value not in {"USER", "HARM"}
        }
        if normalized not in aliases:
            raise DataError("function must be one of: sin, squ, ramp/triangle, puls, nois, dc")
        with self._io_lock:
            self._ensure_identity(write=True)
            self._ensure_configuration_write_allowed()
            snapshot = self._snapshot_basic_status(channel)
            expected = aliases[normalized]
            try:
                self._write(f":SOUR{channel}:FUNC {expected}")
                if self._query_function(channel) != expected:
                    raise InstrumentError("DG4000 function write readback mismatch")
                return self._finish_transaction(
                    channel=channel,
                    check_errors=check_errors,
                )
            except Exception as exc:
                self._recover_configuration_failure(
                    snapshot=snapshot,
                    original_error=exc,
                )
                raise

    def set_amplitude_vpp(
        self,
        channel: int,
        value_vpp: float,
        *,
        check_errors: bool | None = None,
    ) -> SourceStatus:
        _validate_channel(channel)
        value_vpp = _finite_float(value_vpp, field_name="amplitude")
        if value_vpp <= 0:
            raise DataError("amplitude must be > 0")
        with self._io_lock:
            self._ensure_identity(write=True)
            self._ensure_configuration_write_allowed()
            snapshot = self._snapshot_basic_status(channel)
            try:
                self._write(f":SOUR{channel}:VOLT:UNIT VPP")
                if self._query_amplitude_unit(channel) != "VPP":
                    raise InstrumentError(
                        "DG4000 amplitude-unit write readback mismatch"
                    )
                self._write(f":SOUR{channel}:VOLT {value_vpp:.12g}")
                actual = self._query_finite_float(
                    f":SOUR{channel}:VOLT?", field_name="amplitude"
                )
                if not self._numeric_matches(actual, value_vpp):
                    raise InstrumentError("DG4000 amplitude write readback mismatch")
                return self._finish_transaction(
                    channel=channel,
                    check_errors=check_errors,
                )
            except Exception as exc:
                self._recover_configuration_failure(
                    snapshot=snapshot,
                    original_error=exc,
                )
                raise

    def set_square_duty_cycle(
        self,
        channel: int,
        duty_percent: float,
        *,
        check_errors: bool | None = None,
    ) -> SourceStatus:
        _validate_channel(channel)
        duty_percent = _finite_float(duty_percent, field_name="duty cycle percent")
        if duty_percent <= 0 or duty_percent >= 100:
            raise DataError("duty cycle percent must be > 0 and < 100")
        with self._io_lock:
            self._ensure_identity(write=True)
            self._ensure_configuration_write_allowed()
            snapshot = self._snapshot_basic_status(channel)
            try:
                self._write(
                    f":SOUR{channel}:FUNC:SQU:DCYC {duty_percent:.12g}"
                )
                actual = self._query_finite_float(
                    f":SOUR{channel}:FUNC:SQU:DCYC?",
                    field_name="square duty cycle",
                )
                if not self._numeric_matches(actual, duty_percent):
                    raise InstrumentError("DG4000 duty-cycle write readback mismatch")
                return self._finish_transaction(
                    channel=channel,
                    check_errors=check_errors,
                )
            except Exception as exc:
                self._recover_configuration_failure(
                    snapshot=snapshot,
                    original_error=exc,
                )
                raise

    def upload_dg4000_dac14_block(
        self,
        *,
        channel: int,
        block: DG4000DacBlock,
        playback_frequency_hz: float,
        amplitude_vpp: float,
        offset_v: float = 0.0,
        output_on: bool = False,
        check_errors: bool | None = None,
    ) -> SourceStatus:
        _validate_channel(channel)
        _validate_dac14_block(block)
        playback_frequency_hz = _finite_float(
            playback_frequency_hz, field_name="playback frequency"
        )
        amplitude_vpp = _finite_float(amplitude_vpp, field_name="amplitude")
        offset_v = _finite_float(offset_v, field_name="offset")
        if playback_frequency_hz <= 0:
            raise DataError("playback frequency must be > 0")
        if amplitude_vpp <= 0:
            raise DataError("amplitude must be > 0")
        if not callable(getattr(self.transport, "write_bytes", None)):
            raise InstrumentError("transport does not support binary arbitrary waveform upload")
        if not isinstance(output_on, bool):
            raise DataError("output_on must be a boolean")
        with self._io_lock:
            self._ensure_identity(write=True)
            self._ensure_configuration_write_allowed()
            snapshot = self._snapshot_basic_status(channel)
            if snapshot.output != "OFF":
                raise DataError("DG4000 arbitrary upload requires the target output to be OFF")
            if snapshot.frequency_mode != "FIX" or snapshot.sweep_enabled != "OFF":
                raise DataError(
                    "DG4000 arbitrary upload requires FIX mode with sweep disabled"
                )

            try:
                self._write("*CLS")
            except _AmbiguousWriteError:
                self._configuration_writes_blocked = True
                raise InstrumentError(
                    "DG4000 *CLS result was ambiguous before arbitrary upload; no binary "
                    "write was attempted and configuration writes are blocked"
                )

            binary_attempted = False
            try:
                binary_attempted = True
                self._write_bytes(block.command)
                if self._check_errors_enabled(check_errors):
                    self.assert_no_errors()

                self._write(f":SOUR{channel}:FREQ {playback_frequency_hz:.12g}")
                actual_frequency = self._query_finite_float(
                    f":SOUR{channel}:FREQ?", field_name="frequency"
                )
                if not self._numeric_matches(actual_frequency, playback_frequency_hz):
                    raise InstrumentError("DG4000 arbitrary frequency readback mismatch")

                self._write(f":SOUR{channel}:VOLT:UNIT VPP")
                if self._query_amplitude_unit(channel) != "VPP":
                    raise InstrumentError("DG4000 arbitrary amplitude-unit readback mismatch")

                self._write(f":SOUR{channel}:VOLT {amplitude_vpp:.12g}")
                actual_amplitude = self._query_finite_float(
                    f":SOUR{channel}:VOLT?", field_name="amplitude"
                )
                if not self._numeric_matches(actual_amplitude, amplitude_vpp):
                    raise InstrumentError("DG4000 arbitrary amplitude readback mismatch")

                self._write(f":SOUR{channel}:VOLT:OFFS {offset_v:.12g}")
                actual_offset = self._query_finite_float(
                    f":SOUR{channel}:VOLT:OFFS?", field_name="offset"
                )
                if not self._numeric_matches(actual_offset, offset_v):
                    raise InstrumentError("DG4000 arbitrary offset readback mismatch")

                self._write(f":SOUR{channel}:FUNC:SHAP USER")
                if self._query_function(channel) != "USER":
                    raise InstrumentError("DG4000 arbitrary function readback mismatch")

                if output_on:
                    self._write(f":OUTP{channel} ON")
                    if self._query_output(channel) != "ON":
                        raise InstrumentError("DG4000 arbitrary output readback mismatch")

                status = self._finish_transaction(
                    channel=channel,
                    check_errors=check_errors,
                )
                if (
                    status.output != ("ON" if output_on else "OFF")
                    or status.function != "USER"
                    or status.frequency_mode != "FIX"
                    or status.sweep_enabled != "OFF"
                    or status.amplitude_unit != "VPP"
                    or not self._numeric_matches(
                        status.frequency_hz, playback_frequency_hz
                    )
                    or not self._numeric_matches(status.amplitude, amplitude_vpp)
                    or not self._numeric_matches(status.offset_v, offset_v)
                ):
                    raise InstrumentError("DG4000 arbitrary upload final readback mismatch")
                return status
            except Exception as exc:
                if binary_attempted:
                    self._recover_arbitrary_upload_failure(
                        snapshot=snapshot,
                        original_error=exc,
                    )
                raise

    def probe_arbitrary_queries(
        self,
        channel: int,
        candidates: tuple[tuple[str, str], ...] = ARBITRARY_QUERY_CANDIDATES,
    ) -> list[ArbitraryQueryProbeResult]:
        _validate_channel(channel)
        with self._io_lock:
            self._ensure_identity()
            results: list[ArbitraryQueryProbeResult] = []
            self.errors()
            for label, template in candidates:
                command = template.format(channel=channel)
                if not command.strip().endswith("?"):
                    raise DataError("arbitrary probe candidates must be query-only commands")
                response: str | None = None
                exception: str | None = None
                try:
                    response = self.transport.query(command)
                except Exception as exc:
                    exception = f"{type(exc).__name__}: {exc}"
                results.append(
                    ArbitraryQueryProbeResult(
                        label=label,
                        command=command,
                        response=response,
                        errors=self.errors(),
                        exception=exception,
                    )
                )
            return results

    def close(self) -> None:
        with self._io_lock:
            self.transport.close()
