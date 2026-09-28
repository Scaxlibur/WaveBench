from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np

from wavebench.errors import DataError
from wavebench.instruments.dg4000 import DG4000ByteOrder, DG4000DacBlock

DAC14_MIN = 0
DAC14_MAX = 16383


@dataclass(frozen=True)
class ArbitraryWaveform:
    source_path: Path
    samples: np.ndarray
    normalized: np.ndarray
    dac14: np.ndarray
    time_s: np.ndarray | None = None
    sample_rate_hz: float | None = None

    @property
    def points(self) -> int:
        return int(self.normalized.size)

    def summary(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "points": self.points,
            "input_min": float(np.min(self.samples)),
            "input_max": float(np.max(self.samples)),
            "input_mean": float(np.mean(self.samples)),
            "normalized_min": float(np.min(self.normalized)),
            "normalized_max": float(np.max(self.normalized)),
            "dac14_min": int(np.min(self.dac14)),
            "dac14_max": int(np.max(self.dac14)),
            "sample_rate_hz": self.sample_rate_hz,
            "has_time_axis": self.time_s is not None,
        }

    def payload_dict(self, *, name: str, channel: int, amplitude_vpp: float, offset_v: float) -> dict[str, Any]:
        if channel < 1:
            raise DataError("channel must be >= 1")
        clean_name = validate_waveform_name(name)
        if amplitude_vpp <= 0:
            raise DataError("amplitude must be > 0")
        return {
            "format": "wavebench.arbitrary.v1",
            "target": {
                "driver": "dg4202",
                "channel": channel,
                "name": clean_name,
                "amplitude_vpp": float(amplitude_vpp),
                "offset_v": float(offset_v),
                "sample_rate_hz": self.sample_rate_hz,
            },
            "source": self.summary(),
            "payload": {
                "encoding": "dac14_unsigned_integer",
                "range": [DAC14_MIN, DAC14_MAX],
                "values": [int(item) for item in self.dac14.tolist()],
            },
        }


def validate_waveform_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise DataError("waveform name must not be empty")
    if len(normalized) > 32:
        raise DataError("waveform name must be <= 32 characters")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if any(char not in allowed for char in normalized):
        raise DataError("waveform name may only contain letters, digits, underscore, and hyphen")
    return normalized


def build_dg4000_dac14_binary_block(
    waveform: ArbitraryWaveform,
    *,
    byte_order: str | DG4000ByteOrder = DG4000ByteOrder.LITTLE,
) -> DG4000DacBlock:
    """Build a DG4000 DATA:DAC VOLATILE binary block without talking to the instrument.

    The DG4000 guide confirms the 14-bit DAC value range and the IEEE-style
    # length block. It does not explicitly document byte order. 2026-05-01 DG4202
    hardware probing showed DATA:VAL? echoes little-endian DAC blocks correctly.
    """

    try:
        order = DG4000ByteOrder(byte_order)
    except ValueError as exc:
        raise DataError("byte_order must be big or little") from exc
    points = waveform.dac14.astype(np.uint16, copy=False)
    if points.size < 2 or points.size > 16384:
        raise DataError("DG4000 DATA:DAC requires 2..16384 points")
    if np.any(points < DAC14_MIN) or np.any(points > DAC14_MAX):
        raise DataError("DG4000 DATA:DAC values must be within 0..16383")
    endian_dtype = ">u2" if order is DG4000ByteOrder.BIG else "<u2"
    payload = points.astype(endian_dtype, copy=False).tobytes()
    length = str(len(payload)).encode("ascii")
    if len(length) > 9:
        raise DataError("DG4000 binary block payload is too large")
    header = b"#" + str(len(length)).encode("ascii") + length
    command = b":DATA:DAC VOLATILE," + header + payload
    return DG4000DacBlock(command=command, points=int(points.size), data_bytes=len(payload), byte_order=order)


def write_dg4000_dac14_binary_block(
    waveform: ArbitraryWaveform,
    output_path: str | Path,
    *,
    byte_order: str | DG4000ByteOrder = DG4000ByteOrder.LITTLE,
) -> Path:
    block = build_dg4000_dac14_binary_block(waveform, byte_order=byte_order)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(block.command)
    return path


def write_arbitrary_payload_json(
    waveform: ArbitraryWaveform,
    output_path: str | Path,
    *,
    name: str,
    channel: int,
    amplitude_vpp: float,
    offset_v: float,
) -> Path:
    payload = waveform.payload_dict(
        name=name,
        channel=channel,
        amplitude_vpp=amplitude_vpp,
        offset_v=offset_v,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_arbitrary_waveform(
    path: str | Path,
    *,
    sample_rate_hz: float | None = None,
    max_points: int | None = None,
) -> ArbitraryWaveform:
    source_path = Path(path)
    data = _load_array(source_path)
    time_s, samples = _split_waveform_columns(data)
    if sample_rate_hz is not None and sample_rate_hz <= 0:
        raise DataError("sample rate must be > 0")
    if time_s is not None:
        inferred = _infer_sample_rate(time_s)
        sample_rate_hz = sample_rate_hz or inferred
    _validate_samples(samples, max_points=max_points)
    normalized = normalize_peak(samples)
    return ArbitraryWaveform(
        source_path=source_path,
        samples=samples,
        normalized=normalized,
        dac14=normalized_to_dac14(normalized),
        time_s=time_s,
        sample_rate_hz=sample_rate_hz,
    )


def normalize_peak(samples: np.ndarray) -> np.ndarray:
    values = np.asarray(samples, dtype=float)
    peak = float(np.max(np.abs(values)))
    if peak == 0:
        return values.copy()
    if peak <= 1.0:
        return values.copy()
    return values / peak


def normalized_to_dac14(samples: np.ndarray) -> np.ndarray:
    values = np.asarray(samples, dtype=float)
    if np.any(values < -1.0) or np.any(values > 1.0):
        raise DataError("normalized waveform samples must be within [-1, 1]")
    dac = np.rint((values + 1.0) * (DAC14_MAX / 2.0))
    return np.clip(dac, DAC14_MIN, DAC14_MAX).astype(np.uint16)


def _load_array(path: Path) -> np.ndarray:
    suffix = path.suffix.lower()
    try:
        if suffix == ".npy":
            return np.asarray(np.load(path), dtype=float)
        if suffix == ".csv":
            return np.asarray(np.loadtxt(path, delimiter=",", comments="#"), dtype=float)
    except OSError as exc:
        raise DataError(f"cannot read waveform file: {path}") from exc
    except ValueError as exc:
        raise DataError(f"cannot parse waveform file as numeric data: {path}") from exc
    raise DataError("waveform file must be .csv or .npy")


def _split_waveform_columns(data: np.ndarray) -> tuple[np.ndarray | None, np.ndarray]:
    if data.ndim == 0:
        raise DataError("waveform data must contain at least two samples")
    if data.ndim == 1:
        return None, data.astype(float)
    if data.ndim != 2:
        raise DataError("waveform data must be a 1-D array or an Nx1/Nx2 table")
    if data.shape[1] == 1:
        return None, data[:, 0].astype(float)
    if data.shape[1] == 2:
        return data[:, 0].astype(float), data[:, 1].astype(float)
    raise DataError("waveform CSV/NPY table must have one value column or time,value columns")


def _validate_samples(samples: np.ndarray, *, max_points: int | None) -> None:
    if samples.ndim != 1:
        raise DataError("waveform samples must be one-dimensional")
    if samples.size < 2:
        raise DataError("waveform must contain at least two points")
    if max_points is not None:
        if max_points < 2:
            raise DataError("max points must be >= 2")
        if samples.size > max_points:
            raise DataError(f"waveform has {samples.size} points, exceeding max {max_points}")
    if not np.all(np.isfinite(samples)):
        raise DataError("waveform samples must not contain NaN or inf")


def _infer_sample_rate(time_s: np.ndarray) -> float:
    if time_s.size < 2:
        raise DataError("time axis must contain at least two points")
    if not np.all(np.isfinite(time_s)):
        raise DataError("time axis must not contain NaN or inf")
    dt = np.diff(time_s)
    if not np.all(dt > 0):
        raise DataError("time axis must be strictly increasing")
    median_dt = float(np.median(dt))
    if median_dt <= 0:
        raise DataError("time axis sample interval must be positive")
    if np.max(np.abs(dt - median_dt)) > median_dt * 0.01:
        raise DataError("time axis must be uniformly sampled within 1%")
    return 1.0 / median_dt
