from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal

import numpy as np

from wavebench.data.quality import summarize_waveform


SweepMode = Literal["cw", "sweep"]
SweepAxis = Literal["linear", "logarithmic"]
SweepAcquisition = Literal["single", "continuous"]
SweepTrigger = Literal["internal", "external"]
SourceLevelUnit = Literal["dbm", "v", "mv", "unknown"]
MagnitudeUnit = Literal["dbm", "db", "v", "mv", "unknown"]
DeltaMagnitudeUnit = Literal["db", "v", "mv", "unknown"]
MagnitudeSemantics = Literal["absolute", "relative", "linear", "unknown"]
FrequencyAxisSource = Literal["device", "derived", "unknown"]
MeasurementMethod = Literal["instrument", "core"]


@dataclass(frozen=True)
class ScopeIdentitySnapshot:
    manufacturer: str
    model: str
    serial_number: str
    firmware: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopeHealthSnapshot:
    status_byte: int
    operation_condition: int
    questionable_condition: int
    acquisition_available: int
    acquisition_count: int
    sample_rate_hz: float
    error_queue_nonempty: bool
    waiting_for_trigger: bool


@dataclass(frozen=True)
class ScopeAnalogChannelSnapshot:
    channel: int
    enabled: bool
    coupling: str
    range_v: float
    scale_v_per_div: float
    offset_v: float
    position_div: float
    bandwidth_hz: float | None
    polarity: str
    skew_s: float
    label: str
    label_enabled: bool
    overloaded: bool
    acquisition_type: str


@dataclass(frozen=True)
class ScopeTimebaseSnapshot:
    acquisition_time_s: float
    divisions: int
    position_s: float
    range_s: float
    reference_percent: float
    scale_s_per_div: float
    roll_enabled: bool


@dataclass(frozen=True)
class ScopeProbeSnapshot:
    channel: int
    attenuation_factor: float
    bandwidth_hz: float | None
    capacitance_f: float | None
    impedance_ohm: float | None
    name: str
    probe_type: str


@dataclass(frozen=True)
class ScopeWaveformMetadataSnapshot:
    channel: int
    x_start_s: float
    x_stop_s: float
    points: int
    values_per_sample: int | None
    x_increment_s: float
    x_origin_s: float
    y_increment_v: float
    y_origin_v: float
    y_resolution_bits: int


@dataclass(frozen=True)
class ScopeEdgeTriggerSnapshot:
    trigger_type: str
    source_channel: int
    mode: str
    slope: str
    coupling: str
    level_v: float
    hysteresis_mode: str
    holdoff_mode: str
    holdoff_time_s: float


@dataclass(frozen=True)
class ScopeSnapshot:
    identity: ScopeIdentitySnapshot
    health: ScopeHealthSnapshot
    channel: ScopeAnalogChannelSnapshot
    timebase: ScopeTimebaseSnapshot
    probe: ScopeProbeSnapshot
    waveform: ScopeWaveformMetadataSnapshot
    trigger: ScopeEdgeTriggerSnapshot


@dataclass(frozen=True)
class ScopeAcquisitionStatus:
    average_count: int
    average_complete: bool
    segmented_option_installed: bool
    segmented_enabled: bool | None
    segmented_maximum_enabled: bool | None
    segment_capacity: int | None
    segments_available: int | None


@dataclass(frozen=True)
class ScopeHistoryTimestamp:
    position: int
    relative_s: float
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: float


@dataclass(frozen=True)
class ScopeHistoryTimestamps:
    channel: int
    entries: tuple[ScopeHistoryTimestamp, ...]


@dataclass(frozen=True)
class ScopeMeasurementStatistics:
    slot: int
    category: str
    actual: float | None
    average: float | None
    standard_deviation: float | None
    minimum: float | None
    maximum: float | None
    waveform_count: int
    buffered_values: tuple[float, ...] | None = None


def _validate_magnitude_unit_semantics(
    unit: MagnitudeUnit,
    semantics: MagnitudeSemantics,
) -> None:
    valid_pairs = {
        ("dbm", "absolute"),
        ("db", "relative"),
        ("v", "linear"),
        ("mv", "linear"),
        ("unknown", "unknown"),
    }
    if (unit, semantics) not in valid_pairs:
        raise ValueError("incompatible magnitude unit and semantics")


@dataclass(frozen=True)
class SweepPlan:
    mode: SweepMode
    cw_frequency_hz: float | None = None
    start_frequency_hz: float | None = None
    stop_frequency_hz: float | None = None
    center_frequency_hz: float | None = None
    span_frequency_hz: float | None = None
    axis: SweepAxis = "linear"
    sweep_time_s: float | None = None
    acquisition: SweepAcquisition = "single"
    trigger: SweepTrigger = "internal"
    averaging_enabled: bool = False
    average_count: int = 1
    points: int | None = None
    source_output_enabled: bool = False
    source_level: float | None = None
    source_level_unit: SourceLevelUnit | None = None
    source_impedance_ohm: float | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"cw", "sweep"}:
            raise ValueError("sweep mode must be cw or sweep")
        if self.axis not in {"linear", "logarithmic"}:
            raise ValueError("sweep axis must be linear or logarithmic")
        if self.acquisition not in {"single", "continuous"}:
            raise ValueError("sweep acquisition must be single or continuous")
        if self.trigger not in {"internal", "external"}:
            raise ValueError("sweep trigger must be internal or external")

        window_values = (
            self.start_frequency_hz,
            self.stop_frequency_hz,
            self.center_frequency_hz,
            self.span_frequency_hz,
        )
        scalar_values = (
            self.cw_frequency_hz,
            *window_values,
            self.sweep_time_s,
            self.source_level,
            self.source_impedance_ohm,
        )
        if any(value is not None and not isfinite(value) for value in scalar_values):
            raise ValueError("sweep plan numeric values must be finite")
        if self.mode == "cw":
            if self.cw_frequency_hz is None or self.cw_frequency_hz <= 0:
                raise ValueError("cw mode requires cw_frequency_hz > 0")
            if any(value is not None for value in window_values):
                raise ValueError("cw mode cannot define a sweep frequency window")
        else:
            if self.cw_frequency_hz is not None:
                raise ValueError("sweep mode cannot define cw_frequency_hz")
            if not (self.uses_start_stop ^ self.uses_center_span):
                raise ValueError("sweep mode requires exactly one frequency window")
            if self.uses_start_stop:
                assert self.start_frequency_hz is not None
                assert self.stop_frequency_hz is not None
                if self.start_frequency_hz <= 0 or self.stop_frequency_hz <= 0:
                    raise ValueError("sweep frequencies must be > 0")
                if self.start_frequency_hz >= self.stop_frequency_hz:
                    raise ValueError("start_frequency_hz must be < stop_frequency_hz")
            else:
                assert self.center_frequency_hz is not None
                assert self.span_frequency_hz is not None
                if self.center_frequency_hz <= 0 or self.span_frequency_hz <= 0:
                    raise ValueError("center and span frequencies must be > 0")
                if self.span_frequency_hz >= 2 * self.center_frequency_hz:
                    raise ValueError("span_frequency_hz must keep the start frequency > 0")

        if self.sweep_time_s is not None and self.sweep_time_s <= 0:
            raise ValueError("sweep_time_s must be > 0")
        if self.average_count < 1:
            raise ValueError("average_count must be >= 1")
        if self.points is not None and self.points < 2:
            raise ValueError("sweep points must be >= 2")
        if (self.source_level is None) != (self.source_level_unit is None):
            raise ValueError("source_level and source_level_unit must be provided together")
        if self.source_level_unit not in {None, "dbm", "v", "mv", "unknown"}:
            raise ValueError("unsupported source_level_unit")
        if self.source_impedance_ohm is not None and self.source_impedance_ohm <= 0:
            raise ValueError("source_impedance_ohm must be > 0")

    @property
    def uses_start_stop(self) -> bool:
        return self.start_frequency_hz is not None and self.stop_frequency_hz is not None

    @property
    def uses_center_span(self) -> bool:
        return self.center_frequency_hz is not None and self.span_frequency_hz is not None

    @property
    def sweep_time_mode(self) -> Literal["auto", "manual"]:
        return "auto" if self.sweep_time_s is None else "manual"


@dataclass(frozen=True)
class TraceIntegrity:
    complete: bool
    expected_points: int | None
    actual_points: int
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.expected_points is not None and self.expected_points < 1:
            raise ValueError("expected_points must be >= 1")
        if self.actual_points < 0:
            raise ValueError("actual_points must be >= 0")
        if not self.complete and not self.warnings:
            raise ValueError("incomplete trace integrity requires warnings")
        if self.complete and self.expected_points is not None:
            if self.actual_points != self.expected_points:
                raise ValueError("complete trace must match expected_points")


@dataclass(frozen=True)
class FrequencyResponseTrace:
    frequency_hz: np.ndarray | None
    magnitude: np.ndarray | None
    phase_deg: np.ndarray | None
    magnitude_unit: MagnitudeUnit | None
    magnitude_semantics: MagnitudeSemantics | None
    axis_source: FrequencyAxisSource
    integrity: TraceIntegrity
    acquired_at: datetime
    raw_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if self.magnitude is None and self.phase_deg is None:
            raise ValueError("frequency response requires magnitude or phase data")
        if self.axis_source not in {"device", "derived", "unknown"}:
            raise ValueError("unsupported frequency axis source")
        if self.axis_source != "unknown" and self.frequency_hz is None:
            raise ValueError("device or derived axis requires frequency_hz")
        if self.magnitude is not None:
            if self.magnitude_unit is None or self.magnitude_semantics is None:
                raise ValueError("magnitude data requires unit and semantics")
        elif self.magnitude_unit is not None or self.magnitude_semantics is not None:
            raise ValueError("magnitude unit and semantics require magnitude data")
        if self.magnitude_unit not in {None, "dbm", "db", "v", "mv", "unknown"}:
            raise ValueError("unsupported magnitude unit")
        if self.magnitude_semantics not in {
            None,
            "absolute",
            "relative",
            "linear",
            "unknown",
        }:
            raise ValueError("unsupported magnitude semantics")
        if self.magnitude_unit is not None and self.magnitude_semantics is not None:
            _validate_magnitude_unit_semantics(
                self.magnitude_unit,
                self.magnitude_semantics,
            )
        if self.acquired_at.tzinfo is None or self.acquired_at.utcoffset() is None:
            raise ValueError("acquired_at must be timezone-aware")
        if self.raw_evidence_ref is not None and not self.raw_evidence_ref.strip():
            raise ValueError("raw_evidence_ref must be non-empty when provided")

        normalized_arrays: list[np.ndarray] = []
        for name, array in (
            ("frequency_hz", self.frequency_hz),
            ("magnitude", self.magnitude),
            ("phase_deg", self.phase_deg),
        ):
            if array is None:
                continue
            normalized = np.array(array, dtype=np.float64, copy=True)
            normalized.setflags(write=False)
            object.__setattr__(self, name, normalized)
            normalized_arrays.append(normalized)
        for array in normalized_arrays:
            if array.ndim != 1:
                raise ValueError("frequency response arrays must be one-dimensional")
            if not np.all(np.isfinite(array)):
                raise ValueError("frequency response arrays must contain finite values")
        if self.frequency_hz is not None and np.any(self.frequency_hz <= 0):
            raise ValueError("frequency axis values must be positive")
        lengths = {int(array.size) for array in normalized_arrays}
        if len(lengths) != 1:
            raise ValueError("frequency response arrays must have the same number of points")
        if not lengths or next(iter(lengths)) < 1:
            raise ValueError("frequency response must contain at least one point")
        if self.integrity.actual_points != self.point_count:
            raise ValueError("trace integrity actual_points must match response data")

    @property
    def point_count(self) -> int:
        for array in (self.magnitude, self.phase_deg, self.frequency_hz):
            if array is not None:
                return int(array.size)
        return 0


@dataclass(frozen=True)
class SweepAnalyzerSnapshot:
    effective_plan: SweepPlan
    requested_plan: SweepPlan | None = None
    frequency_offset_hz: float = 0.0
    magnitude_measurement_enabled: bool = True
    phase_measurement_enabled: bool = True
    continuous_trace_enabled: bool | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.frequency_offset_hz):
            raise ValueError("frequency_offset_hz must be finite")
        if not self.magnitude_measurement_enabled and not self.phase_measurement_enabled:
            raise ValueError("at least one response measurement must be enabled")


@dataclass(frozen=True)
class MarkerReading:
    index: int
    frequency_hz: float
    magnitude: float | None = None
    magnitude_unit: MagnitudeUnit | None = None
    magnitude_semantics: MagnitudeSemantics | None = None
    phase_deg: float | None = None
    delta_frequency_hz: float | None = None
    delta_magnitude: float | None = None
    delta_magnitude_unit: DeltaMagnitudeUnit | None = None

    def __post_init__(self) -> None:
        if self.index < 1:
            raise ValueError("marker index must be >= 1")
        if self.frequency_hz <= 0:
            raise ValueError("marker frequency_hz must be > 0")
        magnitude_fields = (
            self.magnitude is not None,
            self.magnitude_unit is not None,
            self.magnitude_semantics is not None,
        )
        if any(magnitude_fields) and not all(magnitude_fields):
            raise ValueError("marker magnitude, unit, and semantics must be provided together")
        if self.magnitude_unit not in {None, "dbm", "db", "v", "mv", "unknown"}:
            raise ValueError("unsupported marker magnitude unit")
        if self.magnitude_semantics not in {
            None,
            "absolute",
            "relative",
            "linear",
            "unknown",
        }:
            raise ValueError("unsupported marker magnitude semantics")
        if self.magnitude_unit is not None and self.magnitude_semantics is not None:
            _validate_magnitude_unit_semantics(
                self.magnitude_unit,
                self.magnitude_semantics,
            )
        if (self.delta_magnitude is None) != (self.delta_magnitude_unit is None):
            raise ValueError("marker delta magnitude and unit must be provided together")
        if self.delta_magnitude_unit not in {None, "db", "v", "mv", "unknown"}:
            raise ValueError("unsupported marker delta magnitude unit")
        values = (
            self.frequency_hz,
            self.magnitude,
            self.phase_deg,
            self.delta_frequency_hz,
            self.delta_magnitude,
        )
        if any(value is not None and not np.isfinite(value) for value in values):
            raise ValueError("marker values must be finite")


@dataclass(frozen=True)
class InstrumentMeasurementResult:
    name: str
    value: float
    unit: str
    method: MeasurementMethod
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("measurement name must be non-empty")
        if not self.unit.strip():
            raise ValueError("measurement unit must be non-empty")
        if self.method not in {"instrument", "core"}:
            raise ValueError("measurement method must be instrument or core")
        if not np.isfinite(self.value):
            raise ValueError("measurement value must be finite")


@dataclass(frozen=True)
class WaveformHeader:
    x_start: float
    x_stop: float
    points: int
    segment: int | None = None

    @property
    def x_increment(self) -> float:
        if self.points <= 1:
            return 0.0
        return (self.x_stop - self.x_start) / (self.points - 1)

    @property
    def duration(self) -> float:
        return self.x_stop - self.x_start


@dataclass(frozen=True)
class WaveformData:
    channel: int
    header: WaveformHeader
    voltages_v: np.ndarray

    @property
    def times_s(self) -> np.ndarray:
        if self.header.points <= 1:
            return np.array([self.header.x_start], dtype=np.float64)
        return np.linspace(
            self.header.x_start,
            self.header.x_stop,
            self.header.points,
            dtype=np.float64,
        )

    @property
    def sample_count(self) -> int:
        return int(self.voltages_v.size)

    def summary(
        self,
        *,
        expected_frequency_hz: float | None = None,
        frequency_tolerance_ratio: float = 0.05,
    ) -> dict[str, object]:
        quality = summarize_waveform(
            self.times_s,
            self.voltages_v,
            expected_frequency_hz=expected_frequency_hz,
            frequency_tolerance_ratio=frequency_tolerance_ratio,
        )
        return {
            "channel": self.channel,
            "samples": self.sample_count,
            "x_start_s": self.header.x_start,
            "x_stop_s": self.header.x_stop,
            "x_increment_s": self.header.x_increment,
            **quality.as_dict(),
        }


@dataclass(frozen=True)
class SourceStatus:
    channel: int
    output: str
    function: str
    frequency_hz: float | None
    amplitude: float | None
    amplitude_unit: str | None
    offset_v: float | None
    phase_deg: float | None
    frequency_mode: str
    sweep_enabled: str
    apply_raw: str | None
    square_duty_cycle_percent: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "output": self.output,
            "function": self.function,
            "frequency_hz": self.frequency_hz,
            "amplitude": self.amplitude,
            "amplitude_unit": self.amplitude_unit,
            "offset_v": self.offset_v,
            "phase_deg": self.phase_deg,
            "frequency_mode": self.frequency_mode,
            "sweep_enabled": self.sweep_enabled,
            "apply_raw": self.apply_raw,
            "square_duty_cycle_percent": self.square_duty_cycle_percent,
        }


@dataclass(frozen=True)
class ArbitraryQueryProbeResult:
    label: str
    command: str
    response: str | None
    errors: list[str]
    exception: str | None = None

    @property
    def accepted(self) -> bool:
        if self.exception is not None:
            return False
        return not [
            item
            for item in self.errors
            if not (item.startswith("0") or "No error" in item)
        ]


@dataclass(frozen=True)
class PowerStatus:
    channel: int
    output: str
    mode: str
    rating: str | None
    set_voltage_v: float | None
    set_current_a: float | None
    measured_voltage_v: float | None
    measured_current_a: float | None
    measured_power_w: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "output": self.output,
            "mode": self.mode,
            "rating": self.rating,
            "set_voltage_v": self.set_voltage_v,
            "set_current_a": self.set_current_a,
            "measured_voltage_v": self.measured_voltage_v,
            "measured_current_a": self.measured_current_a,
            "measured_power_w": self.measured_power_w,
        }


@dataclass(frozen=True)
class PowerMeasurement:
    channel: int
    measured_voltage_v: float | None
    measured_current_a: float | None
    measured_power_w: float | None


@dataclass(frozen=True)
class PowerProtectionStatus:
    channel: int
    ovp_enabled: str
    ovp_threshold_v: float | None
    ovp_tripped: str
    ocp_enabled: str
    ocp_threshold_a: float | None
    ocp_tripped: str

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "ovp_enabled": self.ovp_enabled,
            "ovp_threshold_v": self.ovp_threshold_v,
            "ovp_tripped": self.ovp_tripped,
            "ocp_enabled": self.ocp_enabled,
            "ocp_threshold_a": self.ocp_threshold_a,
            "ocp_tripped": self.ocp_tripped,
        }


@dataclass(frozen=True)
class DmmReading:
    function: str
    value: float
    unit: str
    raw: str

    def as_dict(self) -> dict[str, object]:
        return {
            "function": self.function,
            "value": self.value,
            "unit": self.unit,
            "raw": self.raw,
        }
