from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isclose, isfinite
import re
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


ScopeSnapshotFieldV2 = Literal[
    "identity.manufacturer",
    "identity.model",
    "identity.serial_number",
    "identity.firmware",
    "identity.options",
    "health.status_byte",
    "health.operation_condition",
    "health.questionable_condition",
    "health.acquisition_available",
    "health.acquisition_count",
    "health.sample_rate_hz",
    "health.error_queue_nonempty",
    "health.waiting_for_trigger",
    "channel.channel",
    "channel.enabled",
    "channel.coupling",
    "channel.range_v",
    "channel.scale_v_per_div",
    "channel.offset_v",
    "channel.position_div",
    "channel.bandwidth_hz",
    "channel.polarity",
    "channel.skew_s",
    "channel.label",
    "channel.label_enabled",
    "channel.overloaded",
    "channel.acquisition_type",
    "timebase.acquisition_time_s",
    "timebase.divisions",
    "timebase.position_s",
    "timebase.range_s",
    "timebase.reference_percent",
    "timebase.scale_s_per_div",
    "timebase.roll_enabled",
    "probe.channel",
    "probe.attenuation_factor",
    "probe.bandwidth_hz",
    "probe.capacitance_f",
    "probe.impedance_ohm",
    "probe.name",
    "probe.probe_type",
    "waveform.channel",
    "waveform.x_start_s",
    "waveform.x_stop_s",
    "waveform.points",
    "waveform.values_per_sample",
    "waveform.x_increment_s",
    "waveform.x_origin_s",
    "waveform.y_increment_v",
    "waveform.y_origin_v",
    "waveform.y_resolution_bits",
    "trigger.trigger_type",
    "trigger.source_channel",
    "trigger.mode",
    "trigger.slope",
    "trigger.coupling",
    "trigger.level_v",
    "trigger.hysteresis_mode",
    "trigger.holdoff_mode",
    "trigger.holdoff_time_s",
]

SCOPE_SNAPSHOT_V2_FIELD_ORDER: tuple[ScopeSnapshotFieldV2, ...] = (
    "identity.manufacturer",
    "identity.model",
    "identity.serial_number",
    "identity.firmware",
    "identity.options",
    "health.status_byte",
    "health.operation_condition",
    "health.questionable_condition",
    "health.acquisition_available",
    "health.acquisition_count",
    "health.sample_rate_hz",
    "health.error_queue_nonempty",
    "health.waiting_for_trigger",
    "channel.channel",
    "channel.enabled",
    "channel.coupling",
    "channel.range_v",
    "channel.scale_v_per_div",
    "channel.offset_v",
    "channel.position_div",
    "channel.bandwidth_hz",
    "channel.polarity",
    "channel.skew_s",
    "channel.label",
    "channel.label_enabled",
    "channel.overloaded",
    "channel.acquisition_type",
    "timebase.acquisition_time_s",
    "timebase.divisions",
    "timebase.position_s",
    "timebase.range_s",
    "timebase.reference_percent",
    "timebase.scale_s_per_div",
    "timebase.roll_enabled",
    "probe.channel",
    "probe.attenuation_factor",
    "probe.bandwidth_hz",
    "probe.capacitance_f",
    "probe.impedance_ohm",
    "probe.name",
    "probe.probe_type",
    "waveform.channel",
    "waveform.x_start_s",
    "waveform.x_stop_s",
    "waveform.points",
    "waveform.values_per_sample",
    "waveform.x_increment_s",
    "waveform.x_origin_s",
    "waveform.y_increment_v",
    "waveform.y_origin_v",
    "waveform.y_resolution_bits",
    "trigger.trigger_type",
    "trigger.source_channel",
    "trigger.mode",
    "trigger.slope",
    "trigger.coupling",
    "trigger.level_v",
    "trigger.hysteresis_mode",
    "trigger.holdoff_mode",
    "trigger.holdoff_time_s",
)
SCOPE_SNAPSHOT_V2_FIELDS = frozenset(SCOPE_SNAPSHOT_V2_FIELD_ORDER)
_SCOPE_SNAPSHOT_V2_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,63}$")


def _scope_snapshot_v2_required_channel(value: object, *, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")


def _scope_snapshot_v2_optional_int(value: object, *, label: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"{label} must be an integer when provided")


def _scope_snapshot_v2_optional_float(value: object, *, label: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
    ):
        raise ValueError(f"{label} must be finite when provided")


def _scope_snapshot_v2_optional_bool(value: object, *, label: str) -> None:
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{label} must be bool when provided")


def _scope_snapshot_v2_optional_str(value: object, *, label: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{label} must be str when provided")


@dataclass(frozen=True, slots=True)
class ScopeHealthSnapshotV2:
    status_byte: int | None = None
    operation_condition: int | None = None
    questionable_condition: int | None = None
    acquisition_available: int | None = None
    acquisition_count: int | None = None
    sample_rate_hz: float | None = None
    error_queue_nonempty: bool | None = None
    waiting_for_trigger: bool | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("health status_byte", self.status_byte),
            ("health operation_condition", self.operation_condition),
            ("health questionable_condition", self.questionable_condition),
            ("health acquisition_available", self.acquisition_available),
            ("health acquisition_count", self.acquisition_count),
        ):
            _scope_snapshot_v2_optional_int(value, label=label)
        _scope_snapshot_v2_optional_float(self.sample_rate_hz, label="health sample_rate_hz")
        _scope_snapshot_v2_optional_bool(
            self.error_queue_nonempty,
            label="health error_queue_nonempty",
        )
        _scope_snapshot_v2_optional_bool(
            self.waiting_for_trigger,
            label="health waiting_for_trigger",
        )


@dataclass(frozen=True, slots=True)
class ScopeAnalogChannelSnapshotV2:
    channel: int
    enabled: bool | None = None
    coupling: str | None = None
    range_v: float | None = None
    scale_v_per_div: float | None = None
    offset_v: float | None = None
    position_div: float | None = None
    bandwidth_hz: float | None = None
    polarity: str | None = None
    skew_s: float | None = None
    label: str | None = None
    label_enabled: bool | None = None
    overloaded: bool | None = None
    acquisition_type: str | None = None

    def __post_init__(self) -> None:
        _scope_snapshot_v2_required_channel(self.channel, label="snapshot channel")
        _scope_snapshot_v2_optional_bool(self.enabled, label="snapshot channel enabled")
        for label, value in (
            ("snapshot channel coupling", self.coupling),
            ("snapshot channel polarity", self.polarity),
            ("snapshot channel label", self.label),
            ("snapshot channel acquisition_type", self.acquisition_type),
        ):
            _scope_snapshot_v2_optional_str(value, label=label)
        for label, value in (
            ("snapshot channel range_v", self.range_v),
            ("snapshot channel scale_v_per_div", self.scale_v_per_div),
            ("snapshot channel offset_v", self.offset_v),
            ("snapshot channel position_div", self.position_div),
            ("snapshot channel bandwidth_hz", self.bandwidth_hz),
            ("snapshot channel skew_s", self.skew_s),
        ):
            _scope_snapshot_v2_optional_float(value, label=label)
        _scope_snapshot_v2_optional_bool(
            self.label_enabled,
            label="snapshot channel label_enabled",
        )
        _scope_snapshot_v2_optional_bool(self.overloaded, label="snapshot channel overloaded")


@dataclass(frozen=True, slots=True)
class ScopeTimebaseSnapshotV2:
    acquisition_time_s: float | None = None
    divisions: int | None = None
    position_s: float | None = None
    range_s: float | None = None
    reference_percent: float | None = None
    scale_s_per_div: float | None = None
    roll_enabled: bool | None = None

    def __post_init__(self) -> None:
        _scope_snapshot_v2_optional_float(
            self.acquisition_time_s,
            label="snapshot timebase acquisition_time_s",
        )
        _scope_snapshot_v2_optional_int(self.divisions, label="snapshot timebase divisions")
        for label, value in (
            ("snapshot timebase position_s", self.position_s),
            ("snapshot timebase range_s", self.range_s),
            ("snapshot timebase reference_percent", self.reference_percent),
            ("snapshot timebase scale_s_per_div", self.scale_s_per_div),
        ):
            _scope_snapshot_v2_optional_float(value, label=label)
        _scope_snapshot_v2_optional_bool(
            self.roll_enabled,
            label="snapshot timebase roll_enabled",
        )


@dataclass(frozen=True, slots=True)
class ScopeProbeSnapshotV2:
    channel: int
    attenuation_factor: float | None = None
    bandwidth_hz: float | None = None
    capacitance_f: float | None = None
    impedance_ohm: float | None = None
    name: str | None = None
    probe_type: str | None = None

    def __post_init__(self) -> None:
        _scope_snapshot_v2_required_channel(self.channel, label="snapshot probe channel")
        for label, value in (
            ("snapshot probe attenuation_factor", self.attenuation_factor),
            ("snapshot probe bandwidth_hz", self.bandwidth_hz),
            ("snapshot probe capacitance_f", self.capacitance_f),
            ("snapshot probe impedance_ohm", self.impedance_ohm),
        ):
            _scope_snapshot_v2_optional_float(value, label=label)
        _scope_snapshot_v2_optional_str(self.name, label="snapshot probe name")
        _scope_snapshot_v2_optional_str(self.probe_type, label="snapshot probe probe_type")


@dataclass(frozen=True, slots=True)
class ScopeWaveformMetadataSnapshotV2:
    channel: int
    x_start_s: float | None = None
    x_stop_s: float | None = None
    points: int | None = None
    values_per_sample: int | None = None
    x_increment_s: float | None = None
    x_origin_s: float | None = None
    y_increment_v: float | None = None
    y_origin_v: float | None = None
    y_resolution_bits: int | None = None

    def __post_init__(self) -> None:
        _scope_snapshot_v2_required_channel(self.channel, label="snapshot waveform channel")
        for label, value in (
            ("snapshot waveform x_start_s", self.x_start_s),
            ("snapshot waveform x_stop_s", self.x_stop_s),
            ("snapshot waveform x_increment_s", self.x_increment_s),
            ("snapshot waveform x_origin_s", self.x_origin_s),
            ("snapshot waveform y_increment_v", self.y_increment_v),
            ("snapshot waveform y_origin_v", self.y_origin_v),
        ):
            _scope_snapshot_v2_optional_float(value, label=label)
        _scope_snapshot_v2_optional_int(self.points, label="snapshot waveform points")
        _scope_snapshot_v2_optional_int(
            self.values_per_sample,
            label="snapshot waveform values_per_sample",
        )
        _scope_snapshot_v2_optional_int(
            self.y_resolution_bits,
            label="snapshot waveform y_resolution_bits",
        )


@dataclass(frozen=True, slots=True)
class ScopeTriggerSnapshotV2:
    trigger_type: str
    source_channel: int | None = None
    mode: str | None = None
    slope: str | None = None
    coupling: str | None = None
    level_v: float | None = None
    hysteresis_mode: str | None = None
    holdoff_mode: str | None = None
    holdoff_time_s: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.trigger_type, str) or _SCOPE_SNAPSHOT_V2_TOKEN.fullmatch(
            self.trigger_type
        ) is None:
            raise ValueError("snapshot trigger_type must be a safe token")
        _scope_snapshot_v2_optional_int(
            self.source_channel,
            label="snapshot trigger source_channel",
        )
        for label, value in (
            ("snapshot trigger mode", self.mode),
            ("snapshot trigger slope", self.slope),
            ("snapshot trigger coupling", self.coupling),
            ("snapshot trigger hysteresis_mode", self.hysteresis_mode),
            ("snapshot trigger holdoff_mode", self.holdoff_mode),
        ):
            _scope_snapshot_v2_optional_str(value, label=label)
        _scope_snapshot_v2_optional_float(self.level_v, label="snapshot trigger level_v")
        _scope_snapshot_v2_optional_float(
            self.holdoff_time_s,
            label="snapshot trigger holdoff_time_s",
        )


@dataclass(frozen=True, slots=True)
class ScopeSnapshotV2:
    """Portable scope snapshot with explicit availability for every optional leaf."""

    identity: ScopeIdentitySnapshot
    health: ScopeHealthSnapshotV2 | None = None
    channel: ScopeAnalogChannelSnapshotV2 | None = None
    timebase: ScopeTimebaseSnapshotV2 | None = None
    probe: ScopeProbeSnapshotV2 | None = None
    waveform: ScopeWaveformMetadataSnapshotV2 | None = None
    trigger: ScopeTriggerSnapshotV2 | None = None
    unavailable_fields: tuple[ScopeSnapshotFieldV2, ...] = ()
    not_applicable_fields: tuple[ScopeSnapshotFieldV2, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ScopeIdentitySnapshot):
            raise TypeError("snapshot V2 identity has an invalid type")
        identity_values = (
            self.identity.manufacturer,
            self.identity.model,
            self.identity.serial_number,
            self.identity.firmware,
        )
        if any(not isinstance(value, str) or not value.strip() for value in identity_values):
            raise ValueError("snapshot V2 identity fields must be non-empty strings")
        if not isinstance(self.identity.options, tuple) or any(
            not isinstance(value, str) or not value.strip() for value in self.identity.options
        ):
            raise ValueError("snapshot V2 identity options must be a tuple of non-empty strings")
        for label, value, expected in (
            ("health", self.health, ScopeHealthSnapshotV2),
            ("channel", self.channel, ScopeAnalogChannelSnapshotV2),
            ("timebase", self.timebase, ScopeTimebaseSnapshotV2),
            ("probe", self.probe, ScopeProbeSnapshotV2),
            ("waveform", self.waveform, ScopeWaveformMetadataSnapshotV2),
            ("trigger", self.trigger, ScopeTriggerSnapshotV2),
        ):
            if value is not None and not isinstance(value, expected):
                raise TypeError(f"snapshot V2 {label} has an invalid type")
        unavailable = self._availability_paths(
            self.unavailable_fields,
            label="unavailable_fields",
        )
        not_applicable = self._availability_paths(
            self.not_applicable_fields,
            label="not_applicable_fields",
        )
        if set(unavailable) & set(not_applicable):
            raise ValueError("snapshot V2 availability paths must be mutually exclusive")
        missing = {
            field_name
            for field_name, value in self.field_values().items()
            if value is None
        }
        if set(unavailable) | set(not_applicable) != missing:
            raise ValueError(
                "snapshot V2 availability paths must exactly describe missing fields"
            )

    @staticmethod
    def _availability_paths(
        paths: object,
        *,
        label: str,
    ) -> tuple[ScopeSnapshotFieldV2, ...]:
        if not isinstance(paths, tuple):
            raise TypeError(f"snapshot V2 {label} must be a tuple")
        if len(set(paths)) != len(paths):
            raise ValueError(f"snapshot V2 {label} must not contain duplicates")
        if not set(paths) <= SCOPE_SNAPSHOT_V2_FIELDS:
            raise ValueError(f"snapshot V2 {label} contain unsupported paths")
        expected = tuple(field for field in SCOPE_SNAPSHOT_V2_FIELD_ORDER if field in paths)
        if paths != expected:
            raise ValueError(f"snapshot V2 {label} must use stable field order")
        return paths

    def field_values(self) -> dict[ScopeSnapshotFieldV2, object | None]:
        health = self.health
        channel = self.channel
        timebase = self.timebase
        probe = self.probe
        waveform = self.waveform
        trigger = self.trigger
        return {
            "identity.manufacturer": self.identity.manufacturer,
            "identity.model": self.identity.model,
            "identity.serial_number": self.identity.serial_number,
            "identity.firmware": self.identity.firmware,
            "identity.options": self.identity.options,
            "health.status_byte": None if health is None else health.status_byte,
            "health.operation_condition": None if health is None else health.operation_condition,
            "health.questionable_condition": None if health is None else health.questionable_condition,
            "health.acquisition_available": None if health is None else health.acquisition_available,
            "health.acquisition_count": None if health is None else health.acquisition_count,
            "health.sample_rate_hz": None if health is None else health.sample_rate_hz,
            "health.error_queue_nonempty": None if health is None else health.error_queue_nonempty,
            "health.waiting_for_trigger": None if health is None else health.waiting_for_trigger,
            "channel.channel": None if channel is None else channel.channel,
            "channel.enabled": None if channel is None else channel.enabled,
            "channel.coupling": None if channel is None else channel.coupling,
            "channel.range_v": None if channel is None else channel.range_v,
            "channel.scale_v_per_div": None if channel is None else channel.scale_v_per_div,
            "channel.offset_v": None if channel is None else channel.offset_v,
            "channel.position_div": None if channel is None else channel.position_div,
            "channel.bandwidth_hz": None if channel is None else channel.bandwidth_hz,
            "channel.polarity": None if channel is None else channel.polarity,
            "channel.skew_s": None if channel is None else channel.skew_s,
            "channel.label": None if channel is None else channel.label,
            "channel.label_enabled": None if channel is None else channel.label_enabled,
            "channel.overloaded": None if channel is None else channel.overloaded,
            "channel.acquisition_type": None if channel is None else channel.acquisition_type,
            "timebase.acquisition_time_s": None if timebase is None else timebase.acquisition_time_s,
            "timebase.divisions": None if timebase is None else timebase.divisions,
            "timebase.position_s": None if timebase is None else timebase.position_s,
            "timebase.range_s": None if timebase is None else timebase.range_s,
            "timebase.reference_percent": None if timebase is None else timebase.reference_percent,
            "timebase.scale_s_per_div": None if timebase is None else timebase.scale_s_per_div,
            "timebase.roll_enabled": None if timebase is None else timebase.roll_enabled,
            "probe.channel": None if probe is None else probe.channel,
            "probe.attenuation_factor": None if probe is None else probe.attenuation_factor,
            "probe.bandwidth_hz": None if probe is None else probe.bandwidth_hz,
            "probe.capacitance_f": None if probe is None else probe.capacitance_f,
            "probe.impedance_ohm": None if probe is None else probe.impedance_ohm,
            "probe.name": None if probe is None else probe.name,
            "probe.probe_type": None if probe is None else probe.probe_type,
            "waveform.channel": None if waveform is None else waveform.channel,
            "waveform.x_start_s": None if waveform is None else waveform.x_start_s,
            "waveform.x_stop_s": None if waveform is None else waveform.x_stop_s,
            "waveform.points": None if waveform is None else waveform.points,
            "waveform.values_per_sample": None if waveform is None else waveform.values_per_sample,
            "waveform.x_increment_s": None if waveform is None else waveform.x_increment_s,
            "waveform.x_origin_s": None if waveform is None else waveform.x_origin_s,
            "waveform.y_increment_v": None if waveform is None else waveform.y_increment_v,
            "waveform.y_origin_v": None if waveform is None else waveform.y_origin_v,
            "waveform.y_resolution_bits": None if waveform is None else waveform.y_resolution_bits,
            "trigger.trigger_type": None if trigger is None else trigger.trigger_type,
            "trigger.source_channel": None if trigger is None else trigger.source_channel,
            "trigger.mode": None if trigger is None else trigger.mode,
            "trigger.slope": None if trigger is None else trigger.slope,
            "trigger.coupling": None if trigger is None else trigger.coupling,
            "trigger.level_v": None if trigger is None else trigger.level_v,
            "trigger.hysteresis_mode": None if trigger is None else trigger.hysteresis_mode,
            "trigger.holdoff_mode": None if trigger is None else trigger.holdoff_mode,
            "trigger.holdoff_time_s": None if trigger is None else trigger.holdoff_time_s,
        }


@dataclass(frozen=True)
class ScopeAcquisitionStatus:
    average_count: int
    average_complete: bool
    segmented_option_installed: bool
    segmented_enabled: bool | None
    segmented_maximum_enabled: bool | None
    segment_capacity: int | None
    segments_available: int | None


ScopeInputCoupling = Literal["ac", "dc", "gnd", "unknown"]
ScopeInputTermination = Literal["high_z", "50_ohm", "unknown"]
ScopeChannelInputStateFieldV2 = Literal["impedance_ohm"]


@dataclass(frozen=True, slots=True)
class ScopeChannelInputStateV2:
    """Typed read-only coupling and termination state for one analog channel."""

    channel: int
    coupling: ScopeInputCoupling
    termination: ScopeInputTermination
    impedance_ohm: float | None = None
    unavailable_fields: tuple[ScopeChannelInputStateFieldV2, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.channel, bool) or not isinstance(self.channel, int) or self.channel < 1:
            raise ValueError("scope input-state channel must be a positive integer")
        if self.coupling not in {"ac", "dc", "gnd", "unknown"}:
            raise ValueError("scope input-state coupling is invalid")
        if self.termination not in {"high_z", "50_ohm", "unknown"}:
            raise ValueError("scope input-state termination is invalid")
        if not isinstance(self.unavailable_fields, tuple):
            raise TypeError("scope input-state unavailable_fields must be a tuple")
        if self.impedance_ohm is None:
            if self.unavailable_fields != ("impedance_ohm",):
                raise ValueError(
                    "scope input-state missing impedance must be marked unavailable"
                )
            return
        if (
            isinstance(self.impedance_ohm, bool)
            or not isinstance(self.impedance_ohm, (int, float))
            or not isfinite(self.impedance_ohm)
            or self.impedance_ohm <= 0
        ):
            raise ValueError("scope input-state impedance must be a finite positive number")
        if self.unavailable_fields:
            raise ValueError("scope input-state available impedance cannot be unavailable")


ScopeChannelArithmetic = Literal["OFF", "ENVELOPE", "AVERAGE", "SMOOTH", "FILTER"]
ScopeDigitalActivity = Literal["LOW", "HIGH", "TOGGLE"]
ScopeDigitalTechnology = Literal["TTL", "ECL", "CMOS", "MANUAL"]
ScopeDigitalHysteresis = Literal["MAXIMUM", "ROBUST", "NORMAL"]
ScopeDigitalSize = Literal["SMALL", "MEDIUM", "LARGE", "DIV1", "DIV2", "DIV4", "DIV8"]


@dataclass(frozen=True)
class ScopeDigitalChannelStatus:
    channel: int
    group_start_channel: int
    group_stop_channel: int
    displayed: bool
    activity: ScopeDigitalActivity
    technology: ScopeDigitalTechnology
    threshold_v: float
    threshold_coupled: bool
    hysteresis: ScopeDigitalHysteresis
    deskew_s: float
    size: ScopeDigitalSize
    position_div: float
    label: str
    label_enabled: bool


ScopeDigitalThresholdScope = Literal["channel", "pod", "unknown"]
ScopeDigitalActivityV2 = Literal["LOW", "HIGH", "TOGGLE", "unknown"]
ScopeDigitalTechnologyV2 = Literal["TTL", "ECL", "CMOS", "MANUAL", "unknown"]
ScopeDigitalHysteresisV2 = Literal["MAXIMUM", "ROBUST", "NORMAL", "unknown"]
ScopeDigitalSizeV2 = Literal[
    "SMALL",
    "MEDIUM",
    "LARGE",
    "DIV1",
    "DIV2",
    "DIV4",
    "DIV8",
    "unknown",
]
ScopeDigitalStatusFieldV2 = Literal[
    "displayed",
    "position_div",
    "label",
    "label_enabled",
    "activity",
    "technology",
    "hysteresis",
    "pod",
    "pod.threshold_v",
    "pod.threshold_scope",
    "shared",
    "shared.module_present",
    "shared.timing_calibration_s",
    "shared.size",
]

_SCOPE_DIGITAL_STATUS_V2_FIELD_ORDER: tuple[ScopeDigitalStatusFieldV2, ...] = (
    "displayed",
    "position_div",
    "label",
    "label_enabled",
    "activity",
    "technology",
    "hysteresis",
    "pod",
    "pod.threshold_v",
    "pod.threshold_scope",
    "shared",
    "shared.module_present",
    "shared.timing_calibration_s",
    "shared.size",
)
_SCOPE_DIGITAL_STATUS_V2_FIELDS = frozenset(_SCOPE_DIGITAL_STATUS_V2_FIELD_ORDER)


def _scope_v2_nonnegative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _scope_v2_optional_finite(value: object, *, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be finite when provided")
    return float(value)


@dataclass(frozen=True, slots=True)
class ScopeDigitalPodStatusV2:
    start_channel: int
    stop_channel: int
    threshold_v: float | None = None
    threshold_scope: ScopeDigitalThresholdScope | None = None

    def __post_init__(self) -> None:
        _scope_v2_nonnegative_int(self.start_channel, label="digital pod start_channel")
        _scope_v2_nonnegative_int(self.stop_channel, label="digital pod stop_channel")
        if self.start_channel > self.stop_channel:
            raise ValueError("digital pod start_channel must not exceed stop_channel")
        _scope_v2_optional_finite(self.threshold_v, label="digital pod threshold_v")
        if self.threshold_scope is not None and self.threshold_scope not in {
            "channel",
            "pod",
            "unknown",
        }:
            raise ValueError("digital pod threshold_scope is invalid")


@dataclass(frozen=True, slots=True)
class ScopeDigitalSharedStatusV2:
    module_present: bool | None = None
    timing_calibration_s: float | None = None
    size: ScopeDigitalSizeV2 | None = None

    def __post_init__(self) -> None:
        if self.module_present is not None and not isinstance(self.module_present, bool):
            raise ValueError("digital shared module_present must be bool when provided")
        _scope_v2_optional_finite(
            self.timing_calibration_s,
            label="digital shared timing_calibration_s",
        )
        if self.size is not None and self.size not in {
            "SMALL",
            "MEDIUM",
            "LARGE",
            "DIV1",
            "DIV2",
            "DIV4",
            "DIV8",
            "unknown",
        }:
            raise ValueError("digital shared size is invalid")
        if self.module_present is None and self.timing_calibration_s is None and self.size is None:
            raise ValueError("digital shared status must contain at least one available field")


@dataclass(frozen=True, slots=True)
class ScopeDigitalChannelStatusV2:
    """Portable digital channel state that does not invent unavailable device fields."""

    channel: int
    displayed: bool | None = None
    position_div: float | None = None
    label: str | None = None
    label_enabled: bool | None = None
    activity: ScopeDigitalActivityV2 | None = None
    technology: ScopeDigitalTechnologyV2 | None = None
    hysteresis: ScopeDigitalHysteresisV2 | None = None
    pod: ScopeDigitalPodStatusV2 | None = None
    shared: ScopeDigitalSharedStatusV2 | None = None
    unavailable_fields: tuple[ScopeDigitalStatusFieldV2, ...] = ()

    def __post_init__(self) -> None:
        _scope_v2_nonnegative_int(self.channel, label="digital status channel")
        if self.displayed is not None and not isinstance(self.displayed, bool):
            raise ValueError("digital status displayed must be bool when provided")
        _scope_v2_optional_finite(self.position_div, label="digital status position_div")
        if self.label is not None and not isinstance(self.label, str):
            raise ValueError("digital status label must be str when provided")
        if self.label_enabled is not None and not isinstance(self.label_enabled, bool):
            raise ValueError("digital status label_enabled must be bool when provided")
        if self.activity is not None and self.activity not in {"LOW", "HIGH", "TOGGLE", "unknown"}:
            raise ValueError("digital status activity is invalid")
        if self.technology is not None and self.technology not in {
            "TTL",
            "ECL",
            "CMOS",
            "MANUAL",
            "unknown",
        }:
            raise ValueError("digital status technology is invalid")
        if self.hysteresis is not None and self.hysteresis not in {
            "MAXIMUM",
            "ROBUST",
            "NORMAL",
            "unknown",
        }:
            raise ValueError("digital status hysteresis is invalid")
        if self.pod is not None:
            if not isinstance(self.pod, ScopeDigitalPodStatusV2):
                raise TypeError("digital status pod has an invalid type")
            if not self.pod.start_channel <= self.channel <= self.pod.stop_channel:
                raise ValueError("digital pod range must include the requested channel")
        if self.shared is not None and not isinstance(self.shared, ScopeDigitalSharedStatusV2):
            raise TypeError("digital status shared has an invalid type")
        if not isinstance(self.unavailable_fields, tuple):
            raise TypeError("digital status unavailable_fields must be a tuple")
        if len(set(self.unavailable_fields)) != len(self.unavailable_fields):
            raise ValueError("digital status unavailable_fields must not contain duplicates")
        if not set(self.unavailable_fields) <= _SCOPE_DIGITAL_STATUS_V2_FIELDS:
            raise ValueError("digital status unavailable_fields contain unsupported paths")

        expected_unavailable: set[ScopeDigitalStatusFieldV2] = set()
        for field_name, value in (
            ("displayed", self.displayed),
            ("position_div", self.position_div),
            ("label", self.label),
            ("label_enabled", self.label_enabled),
            ("activity", self.activity),
            ("technology", self.technology),
            ("hysteresis", self.hysteresis),
        ):
            if value is None:
                expected_unavailable.add(field_name)  # type: ignore[arg-type]
        if self.pod is None:
            expected_unavailable.add("pod")
        else:
            if self.pod.threshold_v is None:
                expected_unavailable.add("pod.threshold_v")
            if self.pod.threshold_scope is None:
                expected_unavailable.add("pod.threshold_scope")
        if self.shared is None:
            expected_unavailable.add("shared")
        else:
            if self.shared.module_present is None:
                expected_unavailable.add("shared.module_present")
            if self.shared.timing_calibration_s is None:
                expected_unavailable.add("shared.timing_calibration_s")
            if self.shared.size is None:
                expected_unavailable.add("shared.size")
        expected = tuple(
            field for field in _SCOPE_DIGITAL_STATUS_V2_FIELD_ORDER if field in expected_unavailable
        )
        if self.unavailable_fields != expected:
            raise ValueError(
                "digital status unavailable_fields must exactly describe missing fields "
                "in stable order"
            )


@dataclass(frozen=True)
class ScopeDigitalWaveformRequest:
    channels: tuple[int, ...]
    acquisition_stopped: bool

    def __post_init__(self) -> None:
        if not self.channels:
            raise ValueError("digital waveform requires at least one channel")
        if any(
            isinstance(channel, bool) or not isinstance(channel, int) or channel not in range(16)
            for channel in self.channels
        ):
            raise ValueError("digital waveform channels must be integers from 0 through 15")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("digital waveform channels must be unique")
        if self.acquisition_stopped is not True:
            raise ValueError(
                "digital waveform requires explicit confirmation that acquisition is stopped"
            )


@dataclass(frozen=True)
class ScopeDigitalWaveform:
    channels: tuple[int, ...]
    x_start_s: float
    x_stop_s: float
    x_increment_s: float
    samples: np.ndarray

    def __post_init__(self) -> None:
        if not self.channels or len(set(self.channels)) != len(self.channels):
            raise ValueError("digital waveform channels must be nonempty and unique")
        if any(
            isinstance(channel, bool) or not isinstance(channel, int) or channel not in range(16)
            for channel in self.channels
        ):
            raise ValueError("digital waveform channels must be integers from 0 through 15")
        if not all(
            isfinite(value) for value in (self.x_start_s, self.x_stop_s, self.x_increment_s)
        ):
            raise ValueError("digital waveform X-axis values must be finite")
        raw_samples = np.asarray(self.samples)
        if raw_samples.ndim != 1:
            raise ValueError("digital waveform samples must be one-dimensional")
        if raw_samples.size == 0:
            raise ValueError("digital waveform samples must be nonempty")
        if raw_samples.dtype.kind not in {"i", "u"}:
            raise ValueError("digital waveform samples must be integers")
        if np.any(raw_samples < 0) or np.any(raw_samples > np.iinfo(np.uint16).max):
            raise ValueError("digital waveform samples must fit uint16")
        normalized = np.array(raw_samples, dtype=np.uint16, copy=True)
        if self.x_increment_s <= 0:
            raise ValueError("digital waveform x_increment_s must be positive")
        if normalized.size > 1 and self.x_stop_s <= self.x_start_s:
            raise ValueError("digital waveform X axis must be increasing")
        if normalized.size:
            expected_stop_s = self.x_start_s + (normalized.size - 1) * self.x_increment_s
            tolerance_s = max(
                abs(self.x_increment_s) * 1.0e-6,
                abs(self.x_stop_s) * 1.0e-12,
                1.0e-18,
            )
            if not np.isclose(
                expected_stop_s,
                self.x_stop_s,
                rtol=0.0,
                atol=tolerance_s,
            ):
                raise ValueError("digital waveform X axis does not match sample count")
        allowed_mask = sum(1 << channel for channel in self.channels)
        if np.any(np.bitwise_and(normalized, np.uint16(~allowed_mask & 0xFFFF))):
            raise ValueError("digital waveform contains bits outside the requested channels")
        normalized.setflags(write=False)
        object.__setattr__(self, "samples", normalized)

    @property
    def sample_count(self) -> int:
        return int(self.samples.size)

    @property
    def times_s(self) -> np.ndarray:
        if self.sample_count == 0:
            return np.array([], dtype=np.float64)
        return (
            self.x_start_s
            + np.arange(
                self.sample_count,
                dtype=np.float64,
            )
            * self.x_increment_s
        )


@dataclass(frozen=True)
class ScopeAverageCaptureRequest:
    channels: tuple[int, ...]
    average_count: int
    acquisition_stopped: bool

    def __post_init__(self) -> None:
        if not self.channels:
            raise ValueError("average capture requires at least one channel")
        if any(
            isinstance(channel, bool) or not isinstance(channel, int) or channel < 1
            for channel in self.channels
        ):
            raise ValueError("average capture channels must be positive integers")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("average capture channels must be unique")
        if (
            isinstance(self.average_count, bool)
            or not isinstance(self.average_count, int)
            or self.average_count < 2
            or self.average_count > 1024
            or self.average_count & (self.average_count - 1)
        ):
            raise ValueError("average_count must be a power of two from 2 through 1024")
        if self.acquisition_stopped is not True:
            raise ValueError(
                "average capture requires explicit confirmation that acquisition is stopped"
            )


@dataclass(frozen=True)
class ScopeAverageConfiguration:
    average_count: int
    single_count: int
    channel_arithmetic: tuple[tuple[int, ScopeChannelArithmetic], ...]

    def __post_init__(self) -> None:
        if self.average_count < 2 or self.single_count < 1:
            raise ValueError("average configuration counts are out of range")
        channels = tuple(channel for channel, _ in self.channel_arithmetic)
        if not channels or len(set(channels)) != len(channels):
            raise ValueError("average configuration channels must be nonempty and unique")


@dataclass(frozen=True)
class ScopeAverageCaptureResult:
    request: ScopeAverageCaptureRequest
    waveforms: tuple["WaveformData", ...]
    average_complete: bool
    configuration_before: ScopeAverageConfiguration
    configuration_after: ScopeAverageConfiguration
    restored_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        waveform_channels = tuple(waveform.channel for waveform in self.waveforms)
        if waveform_channels != self.request.channels:
            raise ValueError("average capture waveform channels must match the request")
        if not self.average_complete:
            raise ValueError("successful average capture must be complete")
        if self.configuration_after != self.configuration_before:
            raise ValueError("successful average capture must restore its configuration")
        if not self.restored_fields or len(set(self.restored_fields)) != len(
            self.restored_fields
        ):
            raise ValueError("successful average capture requires unique restored fields")


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


ScopeMeasurementSelectorMode = Literal["slot", "item_sources"]
_SCOPE_MEASUREMENT_STATISTICS_V2_TOKEN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,63}$"
)


@dataclass(frozen=True, slots=True)
class ScopeMeasurementSelector:
    slot: int | None = None
    item: str | None = None
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.sources, tuple):
            raise TypeError("measurement selector sources must be a tuple")
        if self.slot is not None:
            if isinstance(self.slot, bool) or not isinstance(self.slot, int) or self.slot < 1:
                raise ValueError("measurement selector slot must be a positive integer")
            if self.item is not None or self.sources:
                raise ValueError("slot measurement selector cannot include item or sources")
            return
        if (
            not isinstance(self.item, str)
            or _SCOPE_MEASUREMENT_STATISTICS_V2_TOKEN.fullmatch(self.item) is None
        ):
            raise ValueError("item measurement selector requires a safe item token")
        if not self.sources:
            raise ValueError("item measurement selector requires at least one source")
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("item measurement selector sources must be unique")
        for source in self.sources:
            if (
                not isinstance(source, str)
                or _SCOPE_MEASUREMENT_STATISTICS_V2_TOKEN.fullmatch(source) is None
            ):
                raise ValueError("item measurement selector sources must be safe tokens")

    @property
    def mode(self) -> ScopeMeasurementSelectorMode:
        return "slot" if self.slot is not None else "item_sources"


@dataclass(frozen=True, slots=True)
class ScopeMeasurementStatisticsRequestV2:
    selector: ScopeMeasurementSelector
    configured: bool
    include_buffer: bool = False
    acquisition_stopped: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.selector, ScopeMeasurementSelector):
            raise TypeError("measurement statistics V2 selector has an invalid type")
        if self.configured is not True:
            raise ValueError("measurement statistics V2 requires configured=True")
        if not isinstance(self.include_buffer, bool):
            raise TypeError("measurement statistics V2 include_buffer must be bool")
        if not isinstance(self.acquisition_stopped, bool):
            raise TypeError("measurement statistics V2 acquisition_stopped must be bool")


@dataclass(frozen=True, slots=True)
class ScopeMeasurementStatisticsV2:
    selector: ScopeMeasurementSelector
    category: str
    actual: float
    average: float
    standard_deviation: float
    minimum: float
    maximum: float
    waveform_count: int
    buffered_values: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.selector, ScopeMeasurementSelector):
            raise TypeError("measurement statistics V2 selector has an invalid type")
        if (
            not isinstance(self.category, str)
            or _SCOPE_MEASUREMENT_STATISTICS_V2_TOKEN.fullmatch(self.category) is None
        ):
            raise ValueError("measurement statistics V2 category must be a safe token")
        for label, value in (
            ("actual", self.actual),
            ("average", self.average),
            ("standard_deviation", self.standard_deviation),
            ("minimum", self.minimum),
            ("maximum", self.maximum),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError(f"measurement statistics V2 {label} must be finite")
        if (
            isinstance(self.waveform_count, bool)
            or not isinstance(self.waveform_count, int)
            or self.waveform_count < 0
        ):
            raise ValueError("measurement statistics V2 waveform_count must be non-negative")
        if self.buffered_values is None:
            return
        if not isinstance(self.buffered_values, tuple):
            raise TypeError("measurement statistics V2 buffered_values must be a tuple")
        for value in self.buffered_values:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError("measurement statistics V2 buffered_values must be finite")


@dataclass(frozen=True)
class ScopeDerivedWaveformMetadata:
    source_kind: Literal["math", "reference"]
    index: int
    source_catalog: str | None
    x_start: float
    x_stop: float
    points: int
    values_per_sample: int | None
    x_increment: float
    x_origin: float
    y_increment: float
    y_origin: float
    y_resolution_bits: int


@dataclass(frozen=True)
class ScopeFftStatus:
    math_index: int
    average_complete: bool
    resolution_bandwidth_hz: float
    sample_rate_hz: float


@dataclass(frozen=True)
class ScopeCursorReadout:
    cursor_index: int
    source: str
    function: str
    result: float | None = None
    x_delta_s: float | None = None
    inverse_x_delta_hz: float | None = None
    y_delta: float | None = None
    inverse_y_delta: float | None = None
    x_ratio: float | None = None
    y_ratio: float | None = None


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
        min_signal_vpp: float = 0.02,
    ) -> dict[str, object]:
        quality = summarize_waveform(
            self.times_s,
            self.voltages_v,
            expected_frequency_hz=expected_frequency_hz,
            frequency_tolerance_ratio=frequency_tolerance_ratio,
            min_signal_vpp=min_signal_vpp,
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
class SourceCouplingProfile:
    """Complete, query-only snapshot of the DG4000 channel-coupling state."""

    base_channel: int
    frequency_enabled: bool
    frequency_deviation_hz: float
    phase_enabled: bool
    phase_deviation_deg: float
    amplitude_enabled: bool
    amplitude_deviation_vpp: float

    def __post_init__(self) -> None:
        _validate_source_coupling_values(
            base_channel=self.base_channel,
            frequency_enabled=self.frequency_enabled,
            frequency_deviation_hz=self.frequency_deviation_hz,
            phase_enabled=self.phase_enabled,
            phase_deviation_deg=self.phase_deviation_deg,
            amplitude_enabled=self.amplitude_enabled,
            amplitude_deviation_vpp=self.amplitude_deviation_vpp,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "base_channel": self.base_channel,
            "frequency_enabled": self.frequency_enabled,
            "frequency_deviation_hz": self.frequency_deviation_hz,
            "phase_enabled": self.phase_enabled,
            "phase_deviation_deg": self.phase_deviation_deg,
            "amplitude_enabled": self.amplitude_enabled,
            "amplitude_deviation_vpp": self.amplitude_deviation_vpp,
        }


@dataclass(frozen=True)
class SourceCouplingConfiguration:
    """Complete target for one controlled DG4000 channel-coupling transaction."""

    base_channel: int
    frequency_enabled: bool
    frequency_deviation_hz: float
    phase_enabled: bool
    phase_deviation_deg: float
    amplitude_enabled: bool
    amplitude_deviation_vpp: float

    def __post_init__(self) -> None:
        _validate_source_coupling_values(
            base_channel=self.base_channel,
            frequency_enabled=self.frequency_enabled,
            frequency_deviation_hz=self.frequency_deviation_hz,
            phase_enabled=self.phase_enabled,
            phase_deviation_deg=self.phase_deviation_deg,
            amplitude_enabled=self.amplitude_enabled,
            amplitude_deviation_vpp=self.amplitude_deviation_vpp,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "base_channel": self.base_channel,
            "frequency_enabled": self.frequency_enabled,
            "frequency_deviation_hz": self.frequency_deviation_hz,
            "phase_enabled": self.phase_enabled,
            "phase_deviation_deg": self.phase_deviation_deg,
            "amplitude_enabled": self.amplitude_enabled,
            "amplitude_deviation_vpp": self.amplitude_deviation_vpp,
        }


@dataclass(frozen=True)
class SourceHarmonicComponent:
    """Read-only amplitude and phase of one H2 through H16 component."""

    order: int
    amplitude_vpp: float
    phase_deg: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.order, bool)
            or not isinstance(self.order, int)
            or not 2 <= self.order <= 16
        ):
            raise ValueError("source harmonic component order must be an integer from 2 to 16")
        if (
            isinstance(self.amplitude_vpp, bool)
            or not isinstance(self.amplitude_vpp, (int, float))
            or not isfinite(self.amplitude_vpp)
            or self.amplitude_vpp < 0
        ):
            raise ValueError("source harmonic component amplitude must be finite and non-negative Vpp")
        if (
            isinstance(self.phase_deg, bool)
            or not isinstance(self.phase_deg, (int, float))
            or not isfinite(self.phase_deg)
            or not 0 <= self.phase_deg <= 360
        ):
            raise ValueError(
                "source harmonic component phase must be finite and from 0 to 360 degrees"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "order": self.order,
            "amplitude_vpp": self.amplitude_vpp,
            "phase_deg": self.phase_deg,
        }


@dataclass(frozen=True)
class SourceHarmonicProfile:
    """Complete, query-only snapshot of one source channel's harmonic generator."""

    channel: int
    order: int
    preset: str
    user_mask: str
    components: tuple[SourceHarmonicComponent, ...]

    def __post_init__(self) -> None:
        if isinstance(self.channel, bool) or not isinstance(self.channel, int) or self.channel < 1:
            raise ValueError("source harmonic channel must be a positive integer")
        _validate_source_harmonic_order(self.order)
        if self.preset not in {"EVEN", "ODD", "ALL", "USER"}:
            raise ValueError("unsupported source harmonic preset")
        if not isinstance(self.user_mask, str) or len(self.user_mask) != 16:
            raise ValueError("source harmonic user mask must be a 16-character X[01]{15} string")
        if self.user_mask[0] != "X" or any(bit not in {"0", "1"} for bit in self.user_mask[1:]):
            raise ValueError("source harmonic user mask must be a 16-character X[01]{15} string")
        if not isinstance(self.components, tuple) or len(self.components) != 15:
            raise ValueError("source harmonic profile requires exactly one component for every H2 to H16")
        if not all(isinstance(component, SourceHarmonicComponent) for component in self.components):
            raise ValueError("source harmonic profile components must be SourceHarmonicComponent")
        if {component.order for component in self.components} != set(range(2, 17)):
            raise ValueError("source harmonic profile components must cover every order from H2 to H16")

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "order": self.order,
            "preset": self.preset,
            "user_mask": self.user_mask,
            "components": tuple(component.as_dict() for component in self.components),
        }


@dataclass(frozen=True)
class SourceHarmonicConfiguration:
    """Low-order preset target for one controlled harmonic configuration transaction."""

    order: int
    preset: str

    def __post_init__(self) -> None:
        _validate_source_harmonic_order(self.order)
        if self.preset not in {"EVEN", "ODD", "ALL"}:
            raise ValueError("source harmonic configuration preset must be EVEN, ODD, or ALL")

    def as_dict(self) -> dict[str, object]:
        return {"order": self.order, "preset": self.preset}


def _validate_source_harmonic_order(order: int) -> None:
    if isinstance(order, bool) or not isinstance(order, int) or not 2 <= order <= 16:
        raise ValueError("source harmonic order must be an integer from 2 to 16")


def _validate_source_coupling_values(
    *,
    base_channel: int,
    frequency_enabled: bool,
    frequency_deviation_hz: float,
    phase_enabled: bool,
    phase_deviation_deg: float,
    amplitude_enabled: bool,
    amplitude_deviation_vpp: float,
) -> None:
    if (
        isinstance(base_channel, bool)
        or not isinstance(base_channel, int)
        or base_channel not in {1, 2}
    ):
        raise ValueError("source coupling base channel must be 1 or 2")
    for name, value in (
        ("frequency_enabled", frequency_enabled),
        ("phase_enabled", phase_enabled),
        ("amplitude_enabled", amplitude_enabled),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"source coupling {name} must be boolean")
    for name, value, limit, unit in (
        ("frequency_deviation_hz", frequency_deviation_hz, 160.0e6, "Hz"),
        ("phase_deviation_deg", phase_deviation_deg, 360.0, "degrees"),
        ("amplitude_deviation_vpp", amplitude_deviation_vpp, 20.0, "Vpp"),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or not 0 <= value <= limit
        ):
            raise ValueError(
                f"source coupling {name} must be finite and from 0 to {limit:g} {unit}"
            )


_SOURCE_INTERNAL_MODULATION_FUNCTIONS = {
    "SINE",
    "SQUARE",
    "TRIANGLE",
    "RAMP",
    "NEGATIVE_RAMP",
    "NOISE",
    "USER",
}


def _validate_source_modulation_common(
    *,
    channel: int | None,
    enabled: bool,
    internal_frequency_hz: float,
    internal_function: str,
) -> None:
    if channel is not None and (
        isinstance(channel, bool) or not isinstance(channel, int) or channel < 1
    ):
        raise ValueError("source modulation channel must be a positive integer")
    if not isinstance(enabled, bool):
        raise ValueError("source modulation enabled must be boolean")
    if (
        isinstance(internal_frequency_hz, bool)
        or not isinstance(internal_frequency_hz, (int, float))
        or not isfinite(internal_frequency_hz)
        or not 0.002 <= internal_frequency_hz <= 50_000.0
    ):
        raise ValueError(
            "source internal modulation frequency must be finite and from 0.002 to 50000 Hz"
        )
    if internal_function not in _SOURCE_INTERNAL_MODULATION_FUNCTIONS:
        raise ValueError("unsupported source internal modulation function")


def _validate_source_modulation_value(
    *,
    name: str,
    value: float,
    minimum: float,
    maximum: float | None,
    unit: str,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        if maximum is None:
            requirement = f"at least {minimum:g} {unit}"
        else:
            requirement = f"from {minimum:g} to {maximum:g} {unit}"
        raise ValueError(f"source modulation {name} must be finite and {requirement}")


@dataclass(frozen=True)
class SourceAmModulationProfile:
    """Complete internal-source AM snapshot for one source channel."""

    channel: int
    enabled: bool
    depth_percent: float
    internal_frequency_hz: float
    internal_function: str

    def __post_init__(self) -> None:
        _validate_source_modulation_common(
            channel=self.channel,
            enabled=self.enabled,
            internal_frequency_hz=self.internal_frequency_hz,
            internal_function=self.internal_function,
        )
        _validate_source_modulation_value(
            name="AM depth",
            value=self.depth_percent,
            minimum=0.0,
            maximum=120.0,
            unit="percent",
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "enabled": self.enabled,
            "depth_percent": self.depth_percent,
            "internal_frequency_hz": self.internal_frequency_hz,
            "internal_function": self.internal_function,
        }


@dataclass(frozen=True)
class SourceAmModulationConfiguration:
    """Complete internal-source AM target for one controlled transaction."""

    enabled: bool
    depth_percent: float
    internal_frequency_hz: float
    internal_function: str

    def __post_init__(self) -> None:
        _validate_source_modulation_common(
            channel=None,
            enabled=self.enabled,
            internal_frequency_hz=self.internal_frequency_hz,
            internal_function=self.internal_function,
        )
        _validate_source_modulation_value(
            name="AM depth",
            value=self.depth_percent,
            minimum=0.0,
            maximum=120.0,
            unit="percent",
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "depth_percent": self.depth_percent,
            "internal_frequency_hz": self.internal_frequency_hz,
            "internal_function": self.internal_function,
        }


@dataclass(frozen=True)
class SourceFmModulationProfile:
    """Complete internal-source FM snapshot for one source channel."""

    channel: int
    enabled: bool
    deviation_hz: float
    internal_frequency_hz: float
    internal_function: str

    def __post_init__(self) -> None:
        _validate_source_modulation_common(
            channel=self.channel,
            enabled=self.enabled,
            internal_frequency_hz=self.internal_frequency_hz,
            internal_function=self.internal_function,
        )
        _validate_source_modulation_value(
            name="FM deviation", value=self.deviation_hz, minimum=0.0, maximum=None, unit="Hz"
        )
        if self.deviation_hz == 0:
            raise ValueError("source modulation FM deviation must be positive")

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "enabled": self.enabled,
            "deviation_hz": self.deviation_hz,
            "internal_frequency_hz": self.internal_frequency_hz,
            "internal_function": self.internal_function,
        }


@dataclass(frozen=True)
class SourceFmModulationConfiguration:
    """Complete internal-source FM target for one controlled transaction."""

    enabled: bool
    deviation_hz: float
    internal_frequency_hz: float
    internal_function: str

    def __post_init__(self) -> None:
        _validate_source_modulation_common(
            channel=None,
            enabled=self.enabled,
            internal_frequency_hz=self.internal_frequency_hz,
            internal_function=self.internal_function,
        )
        _validate_source_modulation_value(
            name="FM deviation", value=self.deviation_hz, minimum=0.0, maximum=None, unit="Hz"
        )
        if self.deviation_hz == 0:
            raise ValueError("source modulation FM deviation must be positive")

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "deviation_hz": self.deviation_hz,
            "internal_frequency_hz": self.internal_frequency_hz,
            "internal_function": self.internal_function,
        }


@dataclass(frozen=True)
class SourcePmModulationProfile:
    """Complete internal-source PM snapshot for one source channel."""

    channel: int
    enabled: bool
    deviation_deg: float
    internal_frequency_hz: float
    internal_function: str

    def __post_init__(self) -> None:
        _validate_source_modulation_common(
            channel=self.channel,
            enabled=self.enabled,
            internal_frequency_hz=self.internal_frequency_hz,
            internal_function=self.internal_function,
        )
        _validate_source_modulation_value(
            name="PM deviation",
            value=self.deviation_deg,
            minimum=0.0,
            maximum=360.0,
            unit="degrees",
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "enabled": self.enabled,
            "deviation_deg": self.deviation_deg,
            "internal_frequency_hz": self.internal_frequency_hz,
            "internal_function": self.internal_function,
        }


@dataclass(frozen=True)
class SourcePmModulationConfiguration:
    """Complete internal-source PM target for one controlled transaction."""

    enabled: bool
    deviation_deg: float
    internal_frequency_hz: float
    internal_function: str

    def __post_init__(self) -> None:
        _validate_source_modulation_common(
            channel=None,
            enabled=self.enabled,
            internal_frequency_hz=self.internal_frequency_hz,
            internal_function=self.internal_function,
        )
        _validate_source_modulation_value(
            name="PM deviation",
            value=self.deviation_deg,
            minimum=0.0,
            maximum=360.0,
            unit="degrees",
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "deviation_deg": self.deviation_deg,
            "internal_frequency_hz": self.internal_frequency_hz,
            "internal_function": self.internal_function,
        }


def _validate_source_pwm_deviation(mode: str, value: float) -> None:
    if mode == "DUTY":
        _validate_source_modulation_value(
            name="PWM duty deviation",
            value=value,
            minimum=0.0,
            maximum=50.0,
            unit="percent",
        )
        return
    if mode == "WIDTH":
        _validate_source_modulation_value(
            name="PWM width deviation",
            value=value,
            minimum=0.0,
            maximum=500_000.0,
            unit="seconds",
        )
        return
    raise ValueError("source PWM deviation mode must be DUTY or WIDTH")


@dataclass(frozen=True)
class SourcePwmModulationProfile:
    """Complete internal-source PWM snapshot with one explicit deviation branch."""

    channel: int
    enabled: bool
    deviation_mode: str
    deviation_value: float
    internal_frequency_hz: float
    internal_function: str

    def __post_init__(self) -> None:
        _validate_source_modulation_common(
            channel=self.channel,
            enabled=self.enabled,
            internal_frequency_hz=self.internal_frequency_hz,
            internal_function=self.internal_function,
        )
        _validate_source_pwm_deviation(self.deviation_mode, self.deviation_value)

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "enabled": self.enabled,
            "deviation_mode": self.deviation_mode,
            "deviation_value": self.deviation_value,
            "internal_frequency_hz": self.internal_frequency_hz,
            "internal_function": self.internal_function,
        }


@dataclass(frozen=True)
class SourcePwmModulationConfiguration:
    """Complete internal-source PWM target with one explicit deviation branch."""

    enabled: bool
    deviation_mode: str
    deviation_value: float
    internal_frequency_hz: float
    internal_function: str

    def __post_init__(self) -> None:
        _validate_source_modulation_common(
            channel=None,
            enabled=self.enabled,
            internal_frequency_hz=self.internal_frequency_hz,
            internal_function=self.internal_function,
        )
        _validate_source_pwm_deviation(self.deviation_mode, self.deviation_value)

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "deviation_mode": self.deviation_mode,
            "deviation_value": self.deviation_value,
            "internal_frequency_hz": self.internal_frequency_hz,
            "internal_function": self.internal_function,
        }


@dataclass(frozen=True)
class SourceChannelProfile:
    """Read-only source-channel context outside the basic restorable state."""

    status: SourceStatus
    load_ohm: float | None
    polarity: str
    noise_enabled: bool
    noise_scale_percent: float
    sync_enabled: bool
    sync_polarity: str
    burst_enabled: bool
    modulation_enabled: bool
    modulation_type: str
    marker_enabled: bool
    pulse_hold: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, SourceStatus):
            raise ValueError("source channel profile status must be SourceStatus")
        if (
            isinstance(self.status.channel, bool)
            or not isinstance(self.status.channel, int)
            or self.status.channel < 1
        ):
            raise ValueError("source channel must be a positive integer")
        if self.load_ohm is not None:
            if isinstance(self.load_ohm, bool) or not isfinite(self.load_ohm):
                raise ValueError(
                    "source load must be finite and positive, or None for high impedance"
                )
            if self.load_ohm <= 0:
                raise ValueError(
                    "source load must be finite and positive, or None for high impedance"
                )
        if self.polarity not in {"NORMAL", "INVERTED"}:
            raise ValueError("unsupported source output polarity")
        for name, value in (
            ("noise_enabled", self.noise_enabled),
            ("sync_enabled", self.sync_enabled),
            ("burst_enabled", self.burst_enabled),
            ("modulation_enabled", self.modulation_enabled),
            ("marker_enabled", self.marker_enabled),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"source {name} must be boolean")
        if (
            isinstance(self.noise_scale_percent, bool)
            or not isfinite(self.noise_scale_percent)
            or not 0 <= self.noise_scale_percent <= 50
        ):
            raise ValueError("source noise scale must be finite and from 0 to 50 percent")
        if self.sync_polarity not in {"POSITIVE", "NEGATIVE"}:
            raise ValueError("unsupported source sync polarity")
        if self.modulation_type not in {
            "AM",
            "FM",
            "PM",
            "ASK",
            "FSK",
            "PSK",
            "PWM",
            "BPSK",
            "QPSK",
            "3FSK",
            "4FSK",
            "OSK",
        }:
            raise ValueError("unsupported source modulation type")
        if self.pulse_hold not in {"DUTY", "WIDTH"}:
            raise ValueError("unsupported source pulse hold mode")

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.as_dict(),
            "load_ohm": self.load_ohm,
            "polarity": self.polarity,
            "noise_enabled": self.noise_enabled,
            "noise_scale_percent": self.noise_scale_percent,
            "sync_enabled": self.sync_enabled,
            "sync_polarity": self.sync_polarity,
            "burst_enabled": self.burst_enabled,
            "modulation_enabled": self.modulation_enabled,
            "modulation_type": self.modulation_type,
            "marker_enabled": self.marker_enabled,
            "pulse_hold": self.pulse_hold,
        }


@dataclass(frozen=True)
class SourceSweepProfile:
    """Complete, query-only snapshot of one source channel's built-in sweep."""

    channel: int
    enabled: bool
    start_hz: float
    stop_hz: float
    center_hz: float
    span_hz: float
    spacing: str
    steps: int
    sweep_time_s: float
    start_hold_s: float
    stop_hold_s: float
    return_time_s: float
    trigger_source: str
    trigger_slope: str
    trigger_out: str
    marker_enabled: bool
    marker_frequency_hz: float

    def __post_init__(self) -> None:
        if isinstance(self.channel, bool) or not isinstance(self.channel, int) or self.channel < 1:
            raise ValueError("source sweep channel must be a positive integer")
        for name, value in (("enabled", self.enabled), ("marker_enabled", self.marker_enabled)):
            if not isinstance(value, bool):
                raise ValueError(f"source sweep {name} must be boolean")
        numeric = (
            ("start_hz", self.start_hz),
            ("stop_hz", self.stop_hz),
            ("center_hz", self.center_hz),
            ("span_hz", self.span_hz),
            ("sweep_time_s", self.sweep_time_s),
            ("start_hold_s", self.start_hold_s),
            ("stop_hold_s", self.stop_hold_s),
            ("return_time_s", self.return_time_s),
            ("marker_frequency_hz", self.marker_frequency_hz),
        )
        for name, value in numeric:
            if isinstance(value, bool) or not isfinite(value):
                raise ValueError(f"source sweep {name} must be finite")
        if self.start_hz <= 0 or self.stop_hz <= 0:
            raise ValueError("source sweep start and stop frequencies must be positive")
        if self.start_hz > self.stop_hz:
            raise ValueError("source sweep start frequency must not exceed stop frequency")
        if not self.start_hz <= self.center_hz <= self.stop_hz:
            raise ValueError("source sweep center frequency must be within start and stop")
        expected_center = (self.start_hz + self.stop_hz) / 2.0
        if not isclose(self.center_hz, expected_center, rel_tol=1.0e-6, abs_tol=1.0e-6):
            raise ValueError("source sweep center frequency is inconsistent with start and stop")
        expected_span = self.stop_hz - self.start_hz
        if self.span_hz < 0 or not isclose(
            self.span_hz,
            expected_span,
            rel_tol=1.0e-6,
            abs_tol=1.0e-6,
        ):
            raise ValueError("source sweep span is inconsistent with start and stop")
        if self.spacing not in {"LINEAR", "LOGARITHMIC", "STEP"}:
            raise ValueError("unsupported source sweep spacing")
        if isinstance(self.steps, bool) or not isinstance(self.steps, int) or not 2 <= self.steps <= 2048:
            raise ValueError("source sweep steps must be an integer from 2 to 2048")
        if not 0.001 <= self.sweep_time_s <= 300:
            raise ValueError("source sweep time must be from 0.001 to 300 seconds")
        for name, value in (
            ("start hold", self.start_hold_s),
            ("stop hold", self.stop_hold_s),
            ("return time", self.return_time_s),
        ):
            if not 0 <= value <= 300:
                raise ValueError(f"source sweep {name} must be from 0 to 300 seconds")
        if self.trigger_source not in {"INTERNAL", "EXTERNAL", "MANUAL"}:
            raise ValueError("unsupported source sweep trigger source")
        if self.trigger_slope not in {"POSITIVE", "NEGATIVE"}:
            raise ValueError("unsupported source sweep trigger slope")
        if self.trigger_out not in {"OFF", "POSITIVE", "NEGATIVE"}:
            raise ValueError("unsupported source sweep trigger output")
        if not self.start_hz <= self.marker_frequency_hz <= self.stop_hz:
            raise ValueError("source sweep marker frequency must be within start and stop")
        if self.marker_enabled and self.spacing == "STEP":
            raise ValueError("source sweep marker cannot be enabled with step spacing")

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "enabled": self.enabled,
            "start_hz": self.start_hz,
            "stop_hz": self.stop_hz,
            "center_hz": self.center_hz,
            "span_hz": self.span_hz,
            "spacing": self.spacing,
            "steps": self.steps,
            "sweep_time_s": self.sweep_time_s,
            "start_hold_s": self.start_hold_s,
            "stop_hold_s": self.stop_hold_s,
            "return_time_s": self.return_time_s,
            "trigger_source": self.trigger_source,
            "trigger_slope": self.trigger_slope,
            "trigger_out": self.trigger_out,
            "marker_enabled": self.marker_enabled,
            "marker_frequency_hz": self.marker_frequency_hz,
        }


@dataclass(frozen=True)
class SourceSweepConfiguration:
    """Complete target for one controlled built-in source sweep transaction."""

    enabled: bool
    spacing: str
    steps: int
    sweep_time_s: float
    start_hold_s: float
    stop_hold_s: float
    return_time_s: float
    trigger_source: str
    trigger_slope: str
    trigger_out: str
    marker_enabled: bool
    marker_frequency_hz: float
    start_hz: float | None = None
    stop_hz: float | None = None
    center_hz: float | None = None
    span_hz: float | None = None

    def __post_init__(self) -> None:
        has_start_stop = self.start_hz is not None or self.stop_hz is not None
        has_center_span = self.center_hz is not None or self.span_hz is not None
        if has_start_stop == has_center_span:
            raise ValueError(
                "source sweep configuration requires exactly one frequency window: "
                "start/stop or center/span"
            )
        if has_start_stop and (self.start_hz is None or self.stop_hz is None):
            raise ValueError("source sweep configuration requires both start_hz and stop_hz")
        if has_center_span and (self.center_hz is None or self.span_hz is None):
            raise ValueError("source sweep configuration requires both center_hz and span_hz")

        for name, value in (
            ("start_hz", self.start_hz),
            ("stop_hz", self.stop_hz),
            ("center_hz", self.center_hz),
            ("span_hz", self.span_hz),
        ):
            if value is not None and (isinstance(value, bool) or not isfinite(value)):
                raise ValueError(f"source sweep configuration {name} must be finite")

        start_hz = self.effective_start_hz
        stop_hz = self.effective_stop_hz
        center_hz = (start_hz + stop_hz) / 2.0
        span_hz = stop_hz - start_hz
        SourceSweepProfile(
            channel=1,
            enabled=self.enabled,
            start_hz=start_hz,
            stop_hz=stop_hz,
            center_hz=center_hz,
            span_hz=span_hz,
            spacing=self.spacing,
            steps=self.steps,
            sweep_time_s=self.sweep_time_s,
            start_hold_s=self.start_hold_s,
            stop_hold_s=self.stop_hold_s,
            return_time_s=self.return_time_s,
            trigger_source=self.trigger_source,
            trigger_slope=self.trigger_slope,
            trigger_out=self.trigger_out,
            marker_enabled=self.marker_enabled,
            marker_frequency_hz=self.marker_frequency_hz,
        )

    @property
    def frequency_basis(self) -> str:
        return "START_STOP" if self.start_hz is not None else "CENTER_SPAN"

    @property
    def effective_start_hz(self) -> float:
        if self.start_hz is not None:
            return self.start_hz
        assert self.center_hz is not None and self.span_hz is not None
        return self.center_hz - self.span_hz / 2.0

    @property
    def effective_stop_hz(self) -> float:
        if self.stop_hz is not None:
            return self.stop_hz
        assert self.center_hz is not None and self.span_hz is not None
        return self.center_hz + self.span_hz / 2.0

    @classmethod
    def from_profile(cls, profile: SourceSweepProfile) -> "SourceSweepConfiguration":
        if not isinstance(profile, SourceSweepProfile):
            raise ValueError("source sweep configuration requires SourceSweepProfile")
        return cls(
            enabled=profile.enabled,
            start_hz=profile.start_hz,
            stop_hz=profile.stop_hz,
            spacing=profile.spacing,
            steps=profile.steps,
            sweep_time_s=profile.sweep_time_s,
            start_hold_s=profile.start_hold_s,
            stop_hold_s=profile.stop_hold_s,
            return_time_s=profile.return_time_s,
            trigger_source=profile.trigger_source,
            trigger_slope=profile.trigger_slope,
            trigger_out=profile.trigger_out,
            marker_enabled=profile.marker_enabled,
            marker_frequency_hz=profile.marker_frequency_hz,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "frequency_basis": self.frequency_basis,
            "start_hz": self.start_hz,
            "stop_hz": self.stop_hz,
            "center_hz": self.center_hz,
            "span_hz": self.span_hz,
            "spacing": self.spacing,
            "steps": self.steps,
            "sweep_time_s": self.sweep_time_s,
            "start_hold_s": self.start_hold_s,
            "stop_hold_s": self.stop_hold_s,
            "return_time_s": self.return_time_s,
            "trigger_source": self.trigger_source,
            "trigger_slope": self.trigger_slope,
            "trigger_out": self.trigger_out,
            "marker_enabled": self.marker_enabled,
            "marker_frequency_hz": self.marker_frequency_hz,
        }


@dataclass(frozen=True)
class SourcePulseProfile:
    """Complete, query-only snapshot of one source channel's pulse shape."""

    channel: int
    hold: str
    width_s: float
    duty_cycle_percent: float
    delay_s: float
    leading_transition_s: float
    trailing_transition_s: float

    def __post_init__(self) -> None:
        if isinstance(self.channel, bool) or not isinstance(self.channel, int) or self.channel < 1:
            raise ValueError("source pulse channel must be a positive integer")
        if self.hold not in {"WIDTH", "DUTY"}:
            raise ValueError("unsupported source pulse hold mode")
        for name, value in (
            ("width_s", self.width_s),
            ("duty_cycle_percent", self.duty_cycle_percent),
            ("delay_s", self.delay_s),
            ("leading_transition_s", self.leading_transition_s),
            ("trailing_transition_s", self.trailing_transition_s),
        ):
            if isinstance(value, bool) or not isfinite(value):
                raise ValueError(f"source pulse {name} must be finite")
        if self.width_s < 4.0e-9:
            raise ValueError("source pulse width must be at least 4 ns")
        if not 0 < self.duty_cycle_percent < 100:
            raise ValueError("source pulse duty cycle must be between 0 and 100 percent")
        if self.delay_s < 0:
            raise ValueError("source pulse delay must be non-negative")
        for name, value in (
            ("leading transition", self.leading_transition_s),
            ("trailing transition", self.trailing_transition_s),
        ):
            if value <= 0:
                raise ValueError(f"source pulse {name} must be positive")
            if value > 0.625 * self.width_s:
                raise ValueError(f"source pulse {name} must not exceed 0.625 times the pulse width")

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "hold": self.hold,
            "width_s": self.width_s,
            "duty_cycle_percent": self.duty_cycle_percent,
            "delay_s": self.delay_s,
            "leading_transition_s": self.leading_transition_s,
            "trailing_transition_s": self.trailing_transition_s,
        }


@dataclass(frozen=True)
class SourcePulseConfiguration:
    """Complete target for one controlled source pulse-shape transaction."""

    hold: str
    delay_s: float
    leading_transition_s: float
    trailing_transition_s: float
    width_s: float | None = None
    duty_cycle_percent: float | None = None

    def __post_init__(self) -> None:
        if self.hold == "WIDTH":
            if self.width_s is None or self.duty_cycle_percent is not None:
                raise ValueError("WIDTH hold requires width_s and forbids duty_cycle_percent")
            width_s = self.width_s
            duty_cycle_percent = 50.0
        elif self.hold == "DUTY":
            if self.duty_cycle_percent is None or self.width_s is not None:
                raise ValueError("DUTY hold requires duty_cycle_percent and forbids width_s")
            width_s = (
                max(
                    self.leading_transition_s,
                    self.trailing_transition_s,
                )
                / 0.625
            )
            duty_cycle_percent = self.duty_cycle_percent
        else:
            raise ValueError("unsupported source pulse hold mode")
        SourcePulseProfile(
            channel=1,
            hold=self.hold,
            width_s=width_s,
            duty_cycle_percent=duty_cycle_percent,
            delay_s=self.delay_s,
            leading_transition_s=self.leading_transition_s,
            trailing_transition_s=self.trailing_transition_s,
        )

    @classmethod
    def from_profile(cls, profile: SourcePulseProfile) -> "SourcePulseConfiguration":
        if not isinstance(profile, SourcePulseProfile):
            raise ValueError("source pulse configuration requires SourcePulseProfile")
        return cls(
            hold=profile.hold,
            width_s=profile.width_s if profile.hold == "WIDTH" else None,
            duty_cycle_percent=(profile.duty_cycle_percent if profile.hold == "DUTY" else None),
            delay_s=profile.delay_s,
            leading_transition_s=profile.leading_transition_s,
            trailing_transition_s=profile.trailing_transition_s,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "hold": self.hold,
            "width_s": self.width_s,
            "duty_cycle_percent": self.duty_cycle_percent,
            "delay_s": self.delay_s,
            "leading_transition_s": self.leading_transition_s,
            "trailing_transition_s": self.trailing_transition_s,
        }


@dataclass(frozen=True)
class SourceBurstProfile:
    """Complete, query-only snapshot of one source channel's burst subsystem."""

    channel: int
    enabled: bool
    mode: str
    cycles: int
    phase_deg: float
    internal_period_s: float
    delay_s: float
    gate_polarity: str
    trigger_source: str
    trigger_slope: str
    trigger_out: str

    def __post_init__(self) -> None:
        if isinstance(self.channel, bool) or not isinstance(self.channel, int) or self.channel < 1:
            raise ValueError("source burst channel must be a positive integer")
        if not isinstance(self.enabled, bool):
            raise ValueError("source burst enabled must be boolean")
        if self.mode not in {"TRIGGERED", "GATED", "INFINITY"}:
            raise ValueError("unsupported source burst mode")
        if isinstance(self.cycles, bool) or not isinstance(self.cycles, int):
            raise ValueError("source burst cycles must be an integer")
        if not 1 <= self.cycles <= 1_000_000:
            raise ValueError("source burst cycles must be from 1 to 1000000")
        if self.trigger_source == "INTERNAL" and self.cycles > 500_000:
            raise ValueError("internal-trigger source burst cycles must not exceed 500000")
        for name, value in (
            ("phase_deg", self.phase_deg),
            ("internal_period_s", self.internal_period_s),
            ("delay_s", self.delay_s),
        ):
            if isinstance(value, bool) or not isfinite(value):
                raise ValueError(f"source burst {name} must be finite")
        if not 0 <= self.phase_deg <= 360:
            raise ValueError("source burst phase must be from 0 to 360 degrees")
        if self.internal_period_s <= 0:
            raise ValueError("source burst internal period must be positive")
        if not 0 <= self.delay_s <= 85:
            raise ValueError("source burst delay must be from 0 to 85 seconds")
        if self.gate_polarity not in {"NORMAL", "INVERTED"}:
            raise ValueError("unsupported source burst gate polarity")
        if self.trigger_source not in {"INTERNAL", "EXTERNAL", "MANUAL"}:
            raise ValueError("unsupported source burst trigger source")
        if self.trigger_slope not in {"POSITIVE", "NEGATIVE"}:
            raise ValueError("unsupported source burst trigger slope")
        if self.trigger_out not in {"OFF", "POSITIVE", "NEGATIVE"}:
            raise ValueError("unsupported source burst trigger output")

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "enabled": self.enabled,
            "mode": self.mode,
            "cycles": self.cycles,
            "phase_deg": self.phase_deg,
            "internal_period_s": self.internal_period_s,
            "delay_s": self.delay_s,
            "gate_polarity": self.gate_polarity,
            "trigger_source": self.trigger_source,
            "trigger_slope": self.trigger_slope,
            "trigger_out": self.trigger_out,
        }


@dataclass(frozen=True)
class SourceBurstConfiguration:
    """Complete target for one controlled source burst transaction."""

    enabled: bool
    mode: str
    cycles: int
    phase_deg: float
    internal_period_s: float
    delay_s: float
    gate_polarity: str
    trigger_source: str
    trigger_slope: str
    trigger_out: str

    def __post_init__(self) -> None:
        SourceBurstProfile(channel=1, **self.as_dict())

    @classmethod
    def from_profile(cls, profile: SourceBurstProfile) -> "SourceBurstConfiguration":
        if not isinstance(profile, SourceBurstProfile):
            raise ValueError("source burst configuration requires SourceBurstProfile")
        values = profile.as_dict()
        values.pop("channel")
        return cls(**values)

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "cycles": self.cycles,
            "phase_deg": self.phase_deg,
            "internal_period_s": self.internal_period_s,
            "delay_s": self.delay_s,
            "gate_polarity": self.gate_polarity,
            "trigger_source": self.trigger_source,
            "trigger_slope": self.trigger_slope,
            "trigger_out": self.trigger_out,
        }


@dataclass(frozen=True)
class SourceCounterMeasurement:
    """One complete frequency-counter result returned by a source instrument."""

    frequency_hz: float
    period_s: float
    duty_cycle_percent: float
    positive_width_s: float
    negative_width_s: float

    def __post_init__(self) -> None:
        for name, value in (
            ("frequency_hz", self.frequency_hz),
            ("period_s", self.period_s),
            ("duty_cycle_percent", self.duty_cycle_percent),
            ("positive_width_s", self.positive_width_s),
            ("negative_width_s", self.negative_width_s),
        ):
            if isinstance(value, bool) or not isfinite(value):
                raise ValueError(f"source counter {name} must be finite")
        if self.frequency_hz <= 0 or self.period_s <= 0:
            raise ValueError("source counter frequency and period must be positive")
        if not 0 <= self.duty_cycle_percent <= 100:
            raise ValueError("source counter duty cycle must be from 0 to 100 percent")
        if self.positive_width_s < 0 or self.negative_width_s < 0:
            raise ValueError("source counter pulse widths must be non-negative")
        if not isclose(
            self.frequency_hz * self.period_s,
            1.0,
            rel_tol=1.0e-3,
            abs_tol=1.0e-6,
        ):
            raise ValueError("source counter frequency and period are inconsistent")
        if not isclose(
            self.positive_width_s + self.negative_width_s,
            self.period_s,
            rel_tol=1.0e-3,
            abs_tol=1.0e-12,
        ):
            raise ValueError("source counter pulse widths are inconsistent with period")
        expected_duty = self.positive_width_s / self.period_s * 100.0
        if not isclose(
            self.duty_cycle_percent,
            expected_duty,
            rel_tol=1.0e-3,
            abs_tol=1.0e-3,
        ):
            raise ValueError("source counter duty cycle is inconsistent with pulse widths")

    def as_dict(self) -> dict[str, float]:
        return {
            "frequency_hz": self.frequency_hz,
            "period_s": self.period_s,
            "duty_cycle_percent": self.duty_cycle_percent,
            "positive_width_s": self.positive_width_s,
            "negative_width_s": self.negative_width_s,
        }


@dataclass(frozen=True)
class SourceCounterProfile:
    """Non-destructive snapshot of a source instrument's counter input."""

    enabled: bool
    measurement: SourceCounterMeasurement | None
    coupling: str
    impedance_ohm: float
    attenuation: int
    gate_time: str
    high_frequency_rejection_enabled: bool
    trigger_level_v: float
    sensitivity_percent: float
    statistics_enabled: bool
    statistics_display: str

    def __post_init__(self) -> None:
        for name, value in (
            ("enabled", self.enabled),
            ("high_frequency_rejection_enabled", self.high_frequency_rejection_enabled),
            ("statistics_enabled", self.statistics_enabled),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"source counter {name} must be boolean")
        if self.enabled != (self.measurement is not None):
            raise ValueError(
                "source counter measurement must be present exactly when the counter is enabled"
            )
        if self.measurement is not None and not isinstance(
            self.measurement, SourceCounterMeasurement
        ):
            raise ValueError("source counter measurement must be SourceCounterMeasurement")
        if self.coupling not in {"AC", "DC"}:
            raise ValueError("unsupported source counter coupling")
        if (
            isinstance(self.impedance_ohm, bool)
            or not isfinite(self.impedance_ohm)
            or self.impedance_ohm not in {50.0, 1_000_000.0}
        ):
            raise ValueError("source counter impedance must be 50 or 1000000 ohms")
        if isinstance(self.attenuation, bool) or self.attenuation not in {1, 10}:
            raise ValueError("source counter attenuation must be 1 or 10")
        if self.gate_time not in {
            "AUTO",
            "USER1",
            "USER2",
            "USER3",
            "USER4",
            "USER5",
            "USER6",
        }:
            raise ValueError("unsupported source counter gate time")
        if (
            isinstance(self.trigger_level_v, bool)
            or not isfinite(self.trigger_level_v)
            or not -2.5 <= self.trigger_level_v <= 2.5
        ):
            raise ValueError("source counter trigger level must be from -2.5 to 2.5 volts")
        if (
            isinstance(self.sensitivity_percent, bool)
            or not isfinite(self.sensitivity_percent)
            or not 0 <= self.sensitivity_percent <= 100
        ):
            raise ValueError("source counter sensitivity must be from 0 to 100 percent")
        if self.statistics_display not in {"DIGITAL", "CURVE"}:
            raise ValueError("unsupported source counter statistics display")

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "measurement": None if self.measurement is None else self.measurement.as_dict(),
            "coupling": self.coupling,
            "impedance_ohm": self.impedance_ohm,
            "attenuation": self.attenuation,
            "gate_time": self.gate_time,
            "high_frequency_rejection_enabled": self.high_frequency_rejection_enabled,
            "trigger_level_v": self.trigger_level_v,
            "sensitivity_percent": self.sensitivity_percent,
            "statistics_enabled": self.statistics_enabled,
            "statistics_display": self.statistics_display,
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


@dataclass(frozen=True)
class DmmMeasurementProfile:
    function: str
    range_code: int | None
    auto_range: bool | None
    impedance: str | None = None

    def __post_init__(self) -> None:
        if not self.function:
            raise ValueError("DMM measurement profile function must be nonempty")
        if self.range_code is not None and (
            isinstance(self.range_code, bool)
            or not isinstance(self.range_code, int)
            or self.range_code < 0
        ):
            raise ValueError("DMM measurement profile range_code must be a nonnegative integer")
        if self.range_code is None and self.auto_range is not None:
            raise ValueError("DMM measurement profile auto_range requires range_code")
        if self.auto_range is not None and not isinstance(self.auto_range, bool):
            raise ValueError("DMM measurement profile auto_range must be boolean when present")
        if self.impedance is not None and not self.impedance.strip():
            raise ValueError("DMM measurement profile impedance must be nonempty when present")

    def as_dict(self) -> dict[str, object]:
        return {
            "function": self.function,
            "range_code": self.range_code,
            "auto_range": self.auto_range,
            "impedance": self.impedance,
        }


@dataclass(frozen=True)
class DmmTriggerStatus:
    source: str
    auto_interval_s: float
    auto_hold: bool
    auto_hold_sensitivity: int
    single_count: int
    external_slope: str
    vmc_polarity: str
    vmc_pulse_width_s: float

    def __post_init__(self) -> None:
        if self.source not in {"AUTO", "SINGLE", "EXT"}:
            raise ValueError("unsupported DMM trigger source")
        if self.auto_interval_s not in {0.1, 0.2, 0.4}:
            raise ValueError("DMM trigger auto interval must be 0.1, 0.2, or 0.4 seconds")
        if not isinstance(self.auto_hold, bool):
            raise ValueError("DMM trigger auto_hold must be boolean")
        if (
            isinstance(self.auto_hold_sensitivity, bool)
            or self.auto_hold_sensitivity not in {0, 1, 2, 3}
        ):
            raise ValueError("DMM trigger auto hold sensitivity must be 0, 1, 2, or 3")
        if (
            isinstance(self.single_count, bool)
            or not isinstance(self.single_count, int)
            or not 1 <= self.single_count <= 5000
        ):
            raise ValueError("DMM trigger single count must be an integer from 1 to 5000")
        if self.external_slope not in {"RISE", "FALL", "HIGH", "LOW"}:
            raise ValueError("unsupported DMM external trigger type")
        if self.vmc_polarity not in {"POS", "NEG"}:
            raise ValueError("unsupported DMM VMC polarity")
        if not isfinite(self.vmc_pulse_width_s) or not 0.001 <= self.vmc_pulse_width_s <= 1.0:
            raise ValueError("DMM trigger VMC pulse width must be from 0.001 to 1.0 seconds")

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "auto_interval_s": self.auto_interval_s,
            "auto_hold": self.auto_hold,
            "auto_hold_sensitivity": self.auto_hold_sensitivity,
            "single_count": self.single_count,
            "external_slope": self.external_slope,
            "vmc_polarity": self.vmc_polarity,
            "vmc_pulse_width_s": self.vmc_pulse_width_s,
        }


@dataclass(frozen=True)
class DmmCalculationStatus:
    function: str
    statistic_count: int
    db_reference: float
    dbm_reference_ohm: float

    def __post_init__(self) -> None:
        if self.function not in {
            "none",
            "null",
            "db",
            "dbm",
            "average",
            "min",
            "max",
            "total",
            "limit",
        }:
            raise ValueError("unsupported DMM calculation function")
        if (
            isinstance(self.statistic_count, bool)
            or not isinstance(self.statistic_count, int)
            or self.statistic_count < 0
        ):
            raise ValueError("DMM statistic_count must be a nonnegative integer")
        if not isfinite(self.db_reference):
            raise ValueError("DMM dB reference must be finite")
        if not isfinite(self.dbm_reference_ohm) or self.dbm_reference_ohm <= 0:
            raise ValueError("DMM dBm reference must be finite and positive")

    def as_dict(self) -> dict[str, object]:
        return {
            "function": self.function,
            "statistic_count": self.statistic_count,
            "db_reference": self.db_reference,
            "dbm_reference_ohm": self.dbm_reference_ohm,
        }


@dataclass(frozen=True)
class DmmCalculationStatistics:
    function: str
    value: float
    count: int

    def __post_init__(self) -> None:
        if self.function not in {"average", "min", "max"}:
            raise ValueError("DMM calculation statistics function must be average, min, or max")
        if not isfinite(self.value):
            raise ValueError("DMM calculation statistic value must be finite")
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 0:
            raise ValueError("DMM calculation statistic count must be a nonnegative integer")

    def as_dict(self) -> dict[str, object]:
        return {"function": self.function, "value": self.value, "count": self.count}


@dataclass(frozen=True)
class DmmSystemInterfaceStatus:
    beeper_enabled: bool
    language: str
    decimal_format: str
    separator_format: str
    display_brightness: int
    scan_board_installed: bool
    lan_interface_installed: bool
    dhcp_enabled: bool
    gpib_address: int
    rs232_baud: int
    rs232_parity: str

    def __post_init__(self) -> None:
        if not isinstance(self.beeper_enabled, bool):
            raise ValueError("DMM beeper_enabled must be boolean")
        if self.language not in {"CHINESE", "ENGLISH"}:
            raise ValueError("unsupported DMM display language")
        if self.decimal_format not in {"COMMA", "DOT"}:
            raise ValueError("unsupported DMM decimal format")
        if self.separator_format not in {"ON", "NONE", "SPACE"}:
            raise ValueError("unsupported DMM separator format")
        if (
            isinstance(self.display_brightness, bool)
            or not isinstance(self.display_brightness, int)
            or not 0 <= self.display_brightness <= 255
        ):
            raise ValueError("DMM display brightness must be an integer from 0 to 255")
        if not isinstance(self.scan_board_installed, bool):
            raise ValueError("DMM scan_board_installed must be boolean")
        if not isinstance(self.lan_interface_installed, bool):
            raise ValueError("DMM lan_interface_installed must be boolean")
        if not isinstance(self.dhcp_enabled, bool):
            raise ValueError("DMM dhcp_enabled must be boolean")
        if (
            isinstance(self.gpib_address, bool)
            or not isinstance(self.gpib_address, int)
            or not 0 <= self.gpib_address <= 30
        ):
            raise ValueError("DMM GPIB address must be an integer from 0 to 30")
        if self.rs232_baud not in {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200}:
            raise ValueError("unsupported DMM RS-232 baud rate")
        if self.rs232_parity not in {"NONE8BITS", "ODD7BITS", "EVEN7BITS"}:
            raise ValueError("unsupported DMM RS-232 parity")

    def as_dict(self) -> dict[str, object]:
        return {
            "beeper_enabled": self.beeper_enabled,
            "language": self.language,
            "decimal_format": self.decimal_format,
            "separator_format": self.separator_format,
            "display_brightness": self.display_brightness,
            "scan_board_installed": self.scan_board_installed,
            "lan_interface_installed": self.lan_interface_installed,
            "dhcp_enabled": self.dhcp_enabled,
            "gpib_address": self.gpib_address,
            "rs232_baud": self.rs232_baud,
            "rs232_parity": self.rs232_parity,
        }


@dataclass(frozen=True)
class DmmVoltageRangeConfiguration:
    function: str
    previous_range_code: int
    range_code: int

    def __post_init__(self) -> None:
        if self.function not in {"dcv", "acv"}:
            raise ValueError("DMM voltage range function must be dcv or acv")
        for name, value in (
            ("previous_range_code", self.previous_range_code),
            ("range_code", self.range_code),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
                raise ValueError(f"DMM voltage range {name} must be an integer from 0 to 4")

    @property
    def changed(self) -> bool:
        return self.previous_range_code != self.range_code

    def as_dict(self) -> dict[str, object]:
        return {
            "function": self.function,
            "previous_range_code": self.previous_range_code,
            "range_code": self.range_code,
            "changed": self.changed,
        }


@dataclass(frozen=True)
class DmmDcvImpedanceConfiguration:
    previous_impedance: str
    impedance: str
    range_code: int

    def __post_init__(self) -> None:
        previous = self.previous_impedance.strip().upper()
        current = self.impedance.strip().upper()
        if previous not in {"10M", "10G"} or current not in {"10M", "10G"}:
            raise ValueError("DMM DCV impedance must be 10M or 10G")
        if isinstance(self.range_code, bool) or not isinstance(self.range_code, int):
            raise ValueError("DMM DCV impedance range_code must be an integer")
        if not 0 <= self.range_code <= 4:
            raise ValueError("DMM DCV impedance range_code must be from 0 to 4")
        if current == "10G" and self.range_code not in {0, 1, 2}:
            raise ValueError("DMM DCV 10G impedance requires range code 0, 1, or 2")
        object.__setattr__(self, "previous_impedance", previous)
        object.__setattr__(self, "impedance", current)

    @property
    def changed(self) -> bool:
        return self.previous_impedance != self.impedance

    def as_dict(self) -> dict[str, object]:
        return {
            "previous_impedance": self.previous_impedance,
            "impedance": self.impedance,
            "range_code": self.range_code,
            "changed": self.changed,
        }
