from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import time

import numpy as np

from wavebench.errors import DataError, InstrumentError, OperationTimeout
from wavebench.instruments.models import WaveformData, WaveformHeader
from wavebench.transport.base import InstrumentTransport


_RAW_POINTS_ALIASES = {"MAX", "DMAX"}
_MAX_BYTE_POINTS_PER_READ = 250_000
_HORIZONTAL_DIVISIONS = 12.0


@dataclass(frozen=True)
class RigolWaveformPreamble:
    format_code: int
    type_code: int
    points: int
    count: int
    x_increment: float
    x_origin: float
    x_reference: float
    y_increment: float
    y_origin: float
    y_reference: float


def parse_rigol_waveform_preamble(response: str) -> RigolWaveformPreamble:
    parts = [item.strip() for item in response.split(",")]
    if len(parts) != 10:
        raise DataError(f"invalid :WAVeform:PREamble? response: {response!r}")
    try:
        preamble = RigolWaveformPreamble(
            format_code=int(float(parts[0])),
            type_code=int(float(parts[1])),
            points=int(float(parts[2])),
            count=int(float(parts[3])),
            x_increment=float(parts[4]),
            x_origin=float(parts[5]),
            x_reference=float(parts[6]),
            y_increment=float(parts[7]),
            y_origin=float(parts[8]),
            y_reference=float(parts[9]),
        )
    except ValueError as exc:
        raise DataError(f"invalid :WAVeform:PREamble? response: {response!r}") from exc
    if preamble.format_code != 0:
        raise DataError(
            f"expected BYTE waveform format code 0, got {preamble.format_code}"
        )
    if preamble.points <= 0:
        raise DataError(f"invalid waveform point count: {preamble.points}")
    if preamble.count <= 0:
        raise DataError(f"invalid waveform average count: {preamble.count}")
    if preamble.x_increment <= 0:
        raise DataError(f"invalid waveform X increment: {preamble.x_increment}")
    if preamble.y_increment <= 0:
        raise DataError(f"invalid waveform Y increment: {preamble.y_increment}")
    return preamble


@dataclass
class DS1104Scope:
    """RIGOL DS1104Z/DS1000Z oscilloscope driver.

    The driver intentionally leaves channel coupling, probe ratio, bandwidth,
    trigger source, and trigger level under front-panel/user control.
    """

    transport: InstrumentTransport
    check_errors_after_ops: bool = True

    def _record_telemetry(self, text: str) -> None:
        recorder = getattr(self.transport, "record_event", None)
        if recorder is not None:
            recorder("telemetry", text)

    def idn(self) -> str:
        return self.transport.query("*IDN?")

    def clear_status(self) -> None:
        self.transport.write("*CLS")

    @staticmethod
    def _validate_channel(channel: int) -> None:
        if channel not in {1, 2, 3, 4}:
            raise DataError("DS1104 channel must be between 1 and 4")

    def channel_coupling(self, channel: int) -> str:
        self._validate_channel(channel)
        return self.transport.query(f":CHANnel{channel}:COUPling?").strip().upper()

    def errors(self, limit: int = 16) -> list[str]:
        errors: list[str] = []
        for _ in range(limit):
            response = self.transport.query(":SYSTem:ERRor?")
            errors.append(response)
            normalized = response.strip().lower()
            if normalized.startswith("0") or "no error" in normalized:
                break
        return errors

    def assert_no_errors(self) -> None:
        errors = self.errors()
        active = [
            item
            for item in errors
            if not (item.strip().startswith("0") or "no error" in item.lower())
        ]
        if active:
            raise InstrumentError("instrument error queue is not empty: " + "; ".join(active))

    def autoscale(self, wait_opc: bool = True, check_errors: bool = True) -> None:
        self.transport.write(":AUToscale")
        if wait_opc:
            self.transport.query_opc()
        if check_errors:
            self.assert_no_errors()

    def set_time_range(self, time_range_s: float) -> None:
        if time_range_s <= 0:
            raise DataError("time range must be > 0")
        scale_s_per_div = time_range_s / _HORIZONTAL_DIVISIONS
        self.transport.write(":TIMebase:MODE MAIN")
        self.transport.write(f":TIMebase:MAIN:SCALe {scale_s_per_div:.12g}")

    def set_vertical_scale(self, channel: int, scale_v_per_div: float) -> None:
        self._validate_channel(channel)
        if scale_v_per_div <= 0:
            raise DataError("vertical scale must be > 0")
        self.transport.write(f":CHANnel{channel}:DISPlay ON")
        self.transport.write(f":CHANnel{channel}:SCALe {scale_v_per_div:.12g}")
        self.transport.write(f":CHANnel{channel}:OFFSet 0")

    def _setup_waveform_transfer(self, channel: int, points: str) -> bool:
        self._validate_channel(channel)
        normalized_points = points.strip().upper()
        raw_mode = normalized_points in _RAW_POINTS_ALIASES
        if normalized_points not in {"DEF", *_RAW_POINTS_ALIASES}:
            raise DataError("DS1104 waveform points must be DEF, MAX, or DMAX")
        self.transport.write(f":CHANnel{channel}:DISPlay ON")
        self.transport.write(f":WAVeform:SOURce CHANnel{channel}")
        self.transport.write(f":WAVeform:MODE {'RAW' if raw_mode else 'NORMal'}")
        self.transport.write(":WAVeform:FORMat BYTE")
        return raw_mode

    def _read_raw_bytes(self, points: int, *, chunked: bool) -> bytes:
        transfer_started = time.perf_counter()
        chunk_size = _MAX_BYTE_POINTS_PER_READ if chunked else points
        chunks: list[bytes] = []
        for start in range(1, points + 1, chunk_size):
            stop = min(points, start + chunk_size - 1)
            self.transport.write(f":WAVeform:STARt {start}")
            self.transport.write(f":WAVeform:STOP {stop}")
            chunk_started = time.perf_counter()
            try:
                chunk = self.transport.query_bin_block(":WAVeform:DATA?")
            except Exception:
                chunk_elapsed = max(time.perf_counter() - chunk_started, 0.0)
                self._record_telemetry(
                    "stage=waveform_chunk status=failed "
                    f"range={start}-{stop} elapsed_ms={chunk_elapsed * 1000.0:.3f}"
                )
                raise
            chunk_elapsed = max(time.perf_counter() - chunk_started, 0.0)
            expected = stop - start + 1
            if len(chunk) != expected:
                label = "waveform chunk" if chunked else "waveform"
                raise DataError(
                    f"{label} length mismatch for points {start}-{stop}: "
                    f"expected {expected}, got {len(chunk)}"
                )
            chunks.append(chunk)
            throughput = (
                len(chunk) / chunk_elapsed / (1024.0 * 1024.0)
                if chunk_elapsed > 0
                else 0.0
            )
            self._record_telemetry(
                "stage=waveform_chunk status=ok "
                f"range={start}-{stop} bytes={len(chunk)} "
                f"elapsed_ms={chunk_elapsed * 1000.0:.3f} "
                f"throughput_mib_s={throughput:.3f}"
            )
        transfer_elapsed = max(time.perf_counter() - transfer_started, 0.0)
        self._record_telemetry(
            "stage=waveform_transfer "
            f"points={points} chunks={len(chunks)} bytes={sum(map(len, chunks))} "
            f"elapsed_ms={transfer_elapsed * 1000.0:.3f}"
        )
        return b"".join(chunks)

    def _read_waveform(self, channel: int, *, raw_mode: bool) -> WaveformData:
        read_started = time.perf_counter()
        preamble_started = time.perf_counter()
        preamble = parse_rigol_waveform_preamble(
            self.transport.query(":WAVeform:PREamble?")
        )
        preamble_elapsed = max(time.perf_counter() - preamble_started, 0.0)
        self._record_telemetry(
            "stage=waveform_preamble "
            f"points={preamble.points} elapsed_ms={preamble_elapsed * 1000.0:.3f}"
        )
        raw = self._read_raw_bytes(preamble.points, chunked=raw_mode)
        convert_started = time.perf_counter()
        voltages = np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
        voltages -= preamble.y_origin + preamble.y_reference
        voltages *= preamble.y_increment
        x_start = preamble.x_origin - preamble.x_reference * preamble.x_increment
        x_stop = x_start + (preamble.points - 1) * preamble.x_increment
        waveform = WaveformData(
            channel=channel,
            header=WaveformHeader(
                x_start=x_start,
                x_stop=x_stop,
                points=preamble.points,
            ),
            voltages_v=voltages,
        )
        convert_elapsed = max(time.perf_counter() - convert_started, 0.0)
        self._record_telemetry(
            "stage=waveform_convert "
            f"points={preamble.points} elapsed_ms={convert_elapsed * 1000.0:.3f}"
        )
        read_elapsed = max(time.perf_counter() - read_started, 0.0)
        self._record_telemetry(
            "stage=waveform_read "
            f"channel={channel} points={preamble.points} "
            f"elapsed_ms={read_elapsed * 1000.0:.3f}"
        )
        return waveform

    def fetch_waveform(
        self, channel: int, points: str = "dmax", check_errors: bool = True
    ) -> WaveformData:
        raw_mode = points.strip().upper() in _RAW_POINTS_ALIASES
        if raw_mode:
            self.transport.write(":STOP")
        raw_mode = self._setup_waveform_transfer(channel=channel, points=points)
        waveform = self._read_waveform(channel=channel, raw_mode=raw_mode)
        if check_errors:
            self.assert_no_errors()
        return waveform

    def capture_waveform(
        self,
        channel: int,
        points: str = "dmax",
        check_errors: bool = True,
        time_range_s: float | None = None,
        vertical_scale_v_per_div: float | None = None,
    ) -> WaveformData:
        self.clear_status()
        self._validate_channel(channel)
        if time_range_s is not None:
            self.set_time_range(time_range_s)
        if vertical_scale_v_per_div is not None:
            self.set_vertical_scale(channel, vertical_scale_v_per_div)
        else:
            self.transport.write(f":CHANnel{channel}:DISPlay ON")
        self.transport.write(":SINGle")
        try:
            self.transport.query_opc()
        except Exception as exc:
            raise OperationTimeout(
                "single acquisition timed out while waiting for *OPC?. "
                "Check trigger source/level, or use `scope fetch` to read the current waveform."
            ) from exc
        raw_mode = self._setup_waveform_transfer(channel=channel, points=points)
        waveform = self._read_waveform(channel=channel, raw_mode=raw_mode)
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
        self.clear_status()
        for channel in channels:
            self._validate_channel(channel)
        if time_range_s is not None:
            self.set_time_range(time_range_s)
        for channel in channels:
            if vertical_scale_v_per_div is not None:
                self.set_vertical_scale(channel, vertical_scale_v_per_div)
            else:
                self.transport.write(f":CHANnel{channel}:DISPlay ON")
        self.transport.write(":SINGle")
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
            raw_mode = self._setup_waveform_transfer(channel=channel, points=points)
            waveform = self._read_waveform(channel=channel, raw_mode=raw_mode)
            waveforms[channel] = waveform
            if on_waveform is not None:
                on_waveform(channel, waveform)
        if check_errors:
            if on_channel_start is not None:
                on_channel_start(None)
            self.assert_no_errors()
        return waveforms

    def screenshot_png(self, *, include_menu: bool = False, color_scheme: str = "COL") -> bytes:
        del include_menu
        color = "ON" if color_scheme.strip().upper() != "MONO" else "OFF"
        data = self.transport.query_bin_block(f":DISPlay:DATA? {color},OFF,PNG")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DataError("screenshot response is not a PNG image")
        return data

    def close(self) -> None:
        self.transport.close()
