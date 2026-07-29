from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

import numpy as np

from wavebench.errors import DataError, InstrumentError, OperationTimeout
from wavebench.instruments.models import WaveformData, WaveformHeader
from wavebench.transport.base import InstrumentTransport

def parse_waveform_header(response: str) -> WaveformHeader:
    parts = [item.strip() for item in response.split(",")]
    if len(parts) < 3:
        raise DataError(f"invalid CHAN:DATA:HEAD? response: {response!r}")
    try:
        x_start = float(parts[0])
        x_stop = float(parts[1])
        points = int(float(parts[2]))
        segment = int(float(parts[3])) if len(parts) >= 4 else None
    except ValueError as exc:
        raise DataError(f"invalid CHAN:DATA:HEAD? response: {response!r}") from exc
    if points <= 0:
        raise DataError(f"invalid waveform point count: {points}")
    return WaveformHeader(x_start=x_start, x_stop=x_stop, points=points, segment=segment)

@dataclass
class RTM2032Scope:
    transport: InstrumentTransport
    check_errors_after_ops: bool = True

    def idn(self) -> str:
        return self.transport.query("*IDN?")

    def clear_status(self) -> None:
        self.transport.write("*CLS")

    def channel_coupling(self, channel: int) -> str:
        if channel < 1:
            raise DataError("channel must be >= 1")
        return self.transport.query(f"CHAN{channel}:COUP?").strip().upper()

    def errors(self, limit: int = 16) -> list[str]:
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

    def autoscale(self, wait_opc: bool = True, check_errors: bool = True) -> None:
        self.transport.write("AUToscale")
        if wait_opc:
            self.transport.query_opc()
        if check_errors:
            self.assert_no_errors()

    def set_time_range(self, time_range_s: float) -> None:
        if time_range_s <= 0:
            raise DataError("time range must be > 0")
        self.transport.write(f"TIMebase:RANGe {time_range_s:.12g}")

    def set_vertical_scale(self, channel: int, scale_v_per_div: float) -> None:
        if channel < 1:
            raise DataError("channel must be >= 1")
        if scale_v_per_div <= 0:
            raise DataError("vertical scale must be > 0")
        self.transport.write(f"CHAN{channel}:STAT ON")
        self.transport.write(f"CHAN{channel}:SCAL {scale_v_per_div:.12g}")
        self.transport.write(f"CHAN{channel}:POS 0")

    def _setup_real_waveform_transfer(self, channel: int, points: str) -> None:
        if channel < 1:
            raise DataError("channel must be >= 1")
        self.transport.write(f"CHAN{channel}:STAT ON")
        self.transport.write("FORM REAL")
        self.transport.write("FORM:BORD LSBF")
        self.transport.write(f"CHAN:DATA:POIN {points.upper()}")

    def _read_waveform(self, channel: int) -> WaveformData:
        header = parse_waveform_header(self.transport.query(f"CHAN{channel}:DATA:HEAD?"))
        voltages = np.asarray(self.transport.query_float_list(f"CHAN{channel}:DATA?"), dtype=np.float64)
        if voltages.size != header.points:
            raise DataError(f"waveform length mismatch: header says {header.points}, got {voltages.size}")
        return WaveformData(channel=channel, header=header, voltages_v=voltages)

    def fetch_waveform(self, channel: int, points: str = "dmax", check_errors: bool = True) -> WaveformData:
        self._setup_real_waveform_transfer(channel=channel, points=points)
        waveform = self._read_waveform(channel=channel)
        if check_errors:
            self.assert_no_errors()
        return waveform

    def capture_waveform(
        self, channel: int, points: str = "dmax", check_errors: bool = True, time_range_s: float | None = None, vertical_scale_v_per_div: float | None = None
    ) -> WaveformData:
        self.transport.write("*CLS")
        if time_range_s is not None:
            self.set_time_range(time_range_s)
        if vertical_scale_v_per_div is not None:
            self.set_vertical_scale(channel, vertical_scale_v_per_div)
        self._setup_real_waveform_transfer(channel=channel, points=points)
        self.transport.write("SINGle")
        try:
            self.transport.query_opc()
        except Exception as exc:
            raise OperationTimeout(
                "single acquisition timed out while waiting for *OPC?. "
                "Check trigger source/level, or use `scope fetch` to read the current waveform."
            ) from exc
        waveform = self._read_waveform(channel=channel)
        if check_errors:
            self.assert_no_errors()
        return waveform

    def capture_waveforms(
        self,
        channels: list[int],
        points: str = "dmax",
        check_errors: bool = True,
        time_range_s: float | None = None,
        vertical_scale_v_per_div: float | None = None,
        on_channel_start: Callable[[int | None], None] | None = None,
        on_waveform: Callable[[int, WaveformData], None] | None = None,
    ) -> dict[int, WaveformData]:
        self.transport.write("*CLS")
        if time_range_s is not None:
            self.set_time_range(time_range_s)
        for channel in channels:
            if channel < 1:
                raise DataError("channel must be >= 1")
            if vertical_scale_v_per_div is not None:
                self.set_vertical_scale(channel, vertical_scale_v_per_div)
            else:
                self.transport.write(f"CHAN{channel}:STAT ON")
            state = self.transport.query(f"CHAN{channel}:STAT?").strip().upper()
            if state not in {"1", "ON"}:
                raise DataError(
                    f"channel {channel} did not become active before single acquisition: "
                    f"CHAN{channel}:STAT? returned {state!r}"
                )
        self.transport.write("SINGle")
        try:
            self.transport.query_opc()
        except Exception as exc:
            raise OperationTimeout(
                "single acquisition timed out while waiting for *OPC?. "
                "Check trigger source/level, or use `scope fetch` to read the current waveform."
            ) from exc
        waveforms: dict[int, WaveformData] = {}
        for channel in channels:
            if on_channel_start is not None:
                on_channel_start(channel)
            self._setup_real_waveform_transfer(channel=channel, points=points)
            waveform = self._read_waveform(channel=channel)
            waveforms[channel] = waveform
            if on_waveform is not None:
                on_waveform(channel, waveform)
        if check_errors:
            if on_channel_start is not None:
                on_channel_start(None)
            self.assert_no_errors()
        return waveforms

    def screenshot_png(self, *, include_menu: bool = False, color_scheme: str = "COL") -> bytes:
        self.transport.write("HCOP:LANG PNG")
        self.transport.write(f"HCOP:COL:SCH {color_scheme}")
        self.transport.write(f"HCOP:MENU {'ON' if include_menu else 'OFF'}")
        data = self.transport.query_bin_block("HCOP:DATA?")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DataError("screenshot response is not a PNG image")
        return data

    def close(self) -> None:
        self.transport.close()
