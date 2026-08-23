"""Public Source V2 contracts.

The snapshot contract is read-only. M5-A additionally freezes typed basic
write requests, results, driver Protocols, and static operation contracts; it
does not provide a Source write entry point or perform instrument I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
import re
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar, runtime_checkable

from .contracts import InstrumentDriver


SOURCE_CONTRACT_VERSION = "wavebench.source.v2"
SOURCE_SNAPSHOT_SCHEMA = "wavebench.source.snapshot.v2"
SOURCE_OPERATION_ARTIFACT_SCHEMA = "wavebench.source.operation.v1"
SOURCE_SNAPSHOT_MIN_CORE_VERSION = "0.8.24"

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_DIST_INFO_EVIDENCE_REF = re.compile(
    r"^dist-info:[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$"
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _require_bool(value: object, label: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")


def _require_int(
    value: object,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be <= {maximum}")


def _require_finite(
    value: object,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be <= {maximum}")


def _require_token(value: object, label: str) -> None:
    if not isinstance(value, str) or _SAFE_TOKEN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a short safe token")


def _require_text(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > 128
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{label} must be non-empty, trimmed safe text")


def _require_text_tuple(values: object, label: str) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{label} must be a tuple")
    for value in values:
        _require_text(value, label)
    if len(set(values)) != len(values) or tuple(sorted(values)) != values:
        raise ValueError(f"{label} must be sorted and unique")


def _require_token_tuple(
    values: object,
    label: str,
    *,
    allow_empty: bool = True,
) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{label} must be a tuple")
    if not allow_empty and not values:
        raise ValueError(f"{label} must not be empty")
    for value in values:
        _require_token(value, label)
    if len(set(values)) != len(values) or tuple(sorted(values)) != values:
        raise ValueError(f"{label} must be sorted and unique")


def _require_evidence_ref_tuple(values: object, label: str) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{label} must be a tuple")
    for value in values:
        valid_dist_info = (
            isinstance(value, str)
            and len(value) <= 240
            and _DIST_INFO_EVIDENCE_REF.fullmatch(value) is not None
            and all(part not in {".", ".."} for part in value.removeprefix("dist-info:").split("/"))
        )
        if not valid_dist_info:
            _require_token(value, label)
    if len(set(values)) != len(values) or tuple(sorted(values)) != values:
        raise ValueError(f"{label} must be sorted and unique")


def _require_enum_tuple(
    values: object,
    enum_type: type[StrEnum],
    label: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{label} must be a tuple")
    if not allow_empty and not values:
        raise ValueError(f"{label} must not be empty")
    if any(not isinstance(value, enum_type) for value in values):
        raise ValueError(f"{label} entries have an invalid type")
    enum_values = tuple(value.value for value in values)
    if len(set(enum_values)) != len(enum_values) or tuple(sorted(enum_values)) != enum_values:
        raise ValueError(f"{label} must be sorted by value and unique")


def _require_positive_channels(values: object, label: str, *, allow_empty: bool = False) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{label} must be a tuple")
    if not allow_empty and not values:
        raise ValueError(f"{label} must not be empty")
    for value in values:
        _require_int(value, label, minimum=1)
    if len(set(values)) != len(values) or tuple(sorted(values)) != values:
        raise ValueError(f"{label} must be sorted and unique")


def _contains_nonfinite(value: object) -> bool:
    if isinstance(value, float):
        return not isfinite(value)
    if isinstance(value, tuple):
        return any(_contains_nonfinite(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return any(_contains_nonfinite(getattr(value, item.name)) for item in fields(value))
    return False


class SourceFeature(StrEnum):
    BASIC = "basic"
    OUTPUT = "output"
    HARMONICS = "harmonics"
    MODULATION = "modulation"
    SWEEP = "sweep"
    BURST = "burst"
    PULSE = "pulse"
    ARBITRARY = "arbitrary"
    COUNTER = "counter"
    REFERENCE_CLOCK = "reference_clock"
    SYNC = "sync"
    CASCADE = "cascade"
    COMBINE = "combine"
    TRACKING = "tracking"
    COUPLING = "coupling"
    COPY = "copy"
    PHASE_RELATION = "phase_relation"
    SHARED_POWER = "shared_power"


class SourceFeatureDirection(StrEnum):
    READ = "read"
    CONFIGURE = "configure"
    ENABLE = "enable"
    DISABLE = "disable"
    ARM = "arm"
    FIRE = "fire"


class SourceWaveformKind(StrEnum):
    SINE = "sine"
    SQUARE = "square"
    RAMP = "ramp"
    PULSE = "pulse"
    NOISE = "noise"
    DC = "dc"
    ARBITRARY = "arbitrary"
    OTHER = "other"


class SourceFrequencyMode(StrEnum):
    FIXED = "fixed"
    SWEEP = "sweep"
    LIST = "list"
    UNKNOWN = "unknown"


class SourceArbitraryPlaybackMode(StrEnum):
    DDS = "dds"
    TRUE_ARB = "true_arb"
    UNKNOWN = "unknown"


class SourceAmplitudeUnit(StrEnum):
    VPP = "vpp"
    VRMS = "vrms"
    DBM = "dbm"
    V = "v"
    UNKNOWN = "unknown"


class SourceOutputPolarity(StrEnum):
    NORMAL = "normal"
    INVERTED = "inverted"
    UNKNOWN = "unknown"


class SourceLoadKind(StrEnum):
    HIGH_IMPEDANCE = "high_impedance"
    RESISTIVE = "resistive"
    UNKNOWN = "unknown"


class HarmonicCompleteness(StrEnum):
    COMPLETE = "complete"
    ACTIVE_ONLY = "active_only"
    SELECTED_ONLY = "selected_only"
    PARTIAL = "partial"


class SourceHarmonicPreset(StrEnum):
    ALL = "all"
    EVEN = "even"
    ODD = "odd"


class ComponentAmplitudeKind(StrEnum):
    ABSOLUTE_VPP = "absolute_vpp"
    RELATIVE_LINEAR = "relative_linear"
    RELATIVE_DB = "relative_db"


class SourceModulationKind(StrEnum):
    AM = "am"
    DSB_AM = "dsb_am"
    FM = "fm"
    PM = "pm"
    PWM = "pwm"
    ASK = "ask"
    FSK = "fsk"
    PSK = "psk"
    OTHER = "other"


class SourceModulationSource(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    CHANNEL = "channel"
    UNKNOWN = "unknown"


class SourceModulationParameterKind(StrEnum):
    DEPTH_PERCENT = "depth_percent"
    FREQUENCY_DEVIATION_HZ = "frequency_deviation_hz"
    PHASE_DEVIATION_DEG = "phase_deviation_deg"
    DUTY_DEVIATION_PERCENT = "duty_deviation_percent"
    SYMBOL_RATE_HZ = "symbol_rate_hz"


class SourceSweepSpacing(StrEnum):
    LINEAR = "linear"
    LOGARITHMIC = "logarithmic"
    STEP = "step"
    UNKNOWN = "unknown"


class SourceTriggerSource(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    MANUAL = "manual"
    BUS = "bus"
    UNKNOWN = "unknown"


class SourceTriggerSlope(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    EITHER = "either"
    UNKNOWN = "unknown"


class SourceTriggerOutput(StrEnum):
    OFF = "off"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


class SourceBurstMode(StrEnum):
    TRIGGERED = "triggered"
    GATED = "gated"
    INFINITY = "infinity"
    UNKNOWN = "unknown"


class SourceGatePolarity(StrEnum):
    NORMAL = "normal"
    INVERTED = "inverted"
    UNKNOWN = "unknown"


class SourcePulseHoldBasis(StrEnum):
    WIDTH = "width"
    DUTY = "duty"
    UNKNOWN = "unknown"


class SourceCounterMeasurementKind(StrEnum):
    FREQUENCY_HZ = "frequency_hz"
    PERIOD_S = "period_s"
    DUTY_PERCENT = "duty_percent"
    POSITIVE_WIDTH_S = "positive_width_s"
    NEGATIVE_WIDTH_S = "negative_width_s"
    UNKNOWN = "unknown"


class SourceInputCoupling(StrEnum):
    AC = "ac"
    DC = "dc"
    UNKNOWN = "unknown"


class SourceReferenceClockMode(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    AUTO = "auto"
    UNKNOWN = "unknown"


class SourceReasonCode(StrEnum):
    DESCRIPTOR_UNSUPPORTED = "descriptor_unsupported"
    SUPPORT_UNKNOWN = "support_unknown"
    NOT_REQUESTED = "not_requested"
    INACTIVE_BY_ANCHOR = "inactive_by_anchor"
    ANCHOR_UNAVAILABLE = "anchor_unavailable"
    RESPONSE_MISSING_FIELD = "response_missing_field"
    RESPONSE_INVALID_VALUE = "response_invalid_value"
    DRIVER_SKIPPED_OPTIONAL = "driver_skipped_optional"
    QUERY_DEADLINE_EXCEEDED = "query_deadline_exceeded"
    QUERY_LIMIT_EXCEEDED = "query_limit_exceeded"
    PROTOCOL_RECORD_INVALID = "protocol_record_invalid"
    REQUIRED_OBSERVATION_MISSING = "required_observation_missing"
    SESSION_NOT_HEALTHY = "session_not_healthy"
    CONSISTENCY_UNPROVEN = "consistency_unproven"
    CONSISTENCY_DRIFTED = "consistency_drifted"


class SupportState(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class Availability(StrEnum):
    VALUE = "value"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"
    NOT_QUERIED = "not_queried"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


T = TypeVar("T")


class PatchAction(StrEnum):
    KEEP = "keep"
    SET = "set"


class PatchMode(StrEnum):
    PATCH = "patch"
    REPLACE_ALL = "replace_all"


@dataclass(frozen=True, slots=True)
class PatchValue(Generic[T]):
    action: PatchAction
    value: T | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, PatchAction):
            raise ValueError("patch action has an invalid type")
        if self.action is PatchAction.SET:
            if self.value is None:
                raise ValueError("SET patch values require a value")
            if _contains_nonfinite(self.value):
                raise ValueError("SET patch values cannot contain non-finite floats")
        elif self.value is not None:
            raise ValueError("KEEP patch values must use value=None")


@dataclass(frozen=True, slots=True)
class Observed(Generic[T]):
    availability: Availability
    value: T | None = None
    reason_code: SourceReasonCode | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.availability, Availability):
            raise ValueError("observed availability has an invalid type")
        _require_evidence_ref_tuple(self.evidence_refs, "observed evidence_refs")
        if self.availability is Availability.VALUE:
            if self.value is None:
                raise ValueError("VALUE observations must carry a value")
            if self.reason_code is not None:
                raise ValueError("VALUE observations cannot carry a reason_code")
            if _contains_nonfinite(self.value):
                raise ValueError("VALUE observations cannot contain non-finite floats")
        else:
            if self.value is not None:
                raise ValueError("non-VALUE observations cannot carry a value")
            if not isinstance(self.reason_code, SourceReasonCode):
                raise ValueError("non-VALUE observations require a registered reason_code")

    @classmethod
    def value_of(cls, value: T, *, evidence_refs: tuple[str, ...] = ()) -> "Observed[T]":
        return cls(Availability.VALUE, value, evidence_refs=evidence_refs)

    @classmethod
    def missing(
        cls,
        availability: Availability,
        reason_code: SourceReasonCode,
    ) -> "Observed[T]":
        if availability is Availability.VALUE:
            raise ValueError("missing observations cannot use VALUE availability")
        return cls(availability=availability, reason_code=reason_code)


@dataclass(frozen=True, slots=True)
class SourceBasicCapabilityProfile:
    waveform_kinds: tuple[SourceWaveformKind, ...]
    frequency_modes: tuple[SourceFrequencyMode, ...]
    amplitude_units: tuple[SourceAmplitudeUnit, ...]
    offset_readable: bool
    phase_readable: bool
    square_duty_readable: bool

    def __post_init__(self) -> None:
        _require_enum_tuple(self.waveform_kinds, SourceWaveformKind, "basic waveform_kinds")
        _require_enum_tuple(self.frequency_modes, SourceFrequencyMode, "basic frequency_modes")
        _require_enum_tuple(self.amplitude_units, SourceAmplitudeUnit, "basic amplitude_units")
        _require_bool(self.offset_readable, "basic offset_readable")
        _require_bool(self.phase_readable, "basic phase_readable")
        _require_bool(self.square_duty_readable, "basic square_duty_readable")


@dataclass(frozen=True, slots=True)
class SourceOutputCapabilityProfile:
    output_readable: bool
    display_load_readable: bool
    polarity_readable: bool

    def __post_init__(self) -> None:
        _require_bool(self.output_readable, "output output_readable")
        _require_bool(self.display_load_readable, "output display_load_readable")
        _require_bool(self.polarity_readable, "output polarity_readable")


@dataclass(frozen=True, slots=True)
class SourceHarmonicCapabilityProfile:
    minimum_order: int
    maximum_order: int
    amplitude_kinds: tuple[ComponentAmplitudeKind, ...]
    completeness_modes: tuple[HarmonicCompleteness, ...]
    presets: tuple[SourceHarmonicPreset, ...] = ()
    configured_order_readable: bool = False
    preset_readable: bool = False

    def __post_init__(self) -> None:
        _require_int(self.minimum_order, "harmonic minimum_order", minimum=2)
        _require_int(self.maximum_order, "harmonic maximum_order", minimum=self.minimum_order)
        _require_enum_tuple(
            self.amplitude_kinds,
            ComponentAmplitudeKind,
            "harmonic amplitude_kinds",
        )
        _require_enum_tuple(
            self.completeness_modes,
            HarmonicCompleteness,
            "harmonic completeness_modes",
        )
        _require_enum_tuple(
            self.presets,
            SourceHarmonicPreset,
            "harmonic presets",
            allow_empty=True,
        )
        _require_bool(self.configured_order_readable, "harmonic configured_order_readable")
        _require_bool(self.preset_readable, "harmonic preset_readable")


@dataclass(frozen=True, slots=True)
class SourceModulationCapabilityProfile:
    kinds: tuple[SourceModulationKind, ...]
    sources: tuple[SourceModulationSource, ...]
    parameter_kinds: tuple[SourceModulationParameterKind, ...]
    inactive_readable: bool

    def __post_init__(self) -> None:
        _require_enum_tuple(self.kinds, SourceModulationKind, "modulation kinds")
        _require_enum_tuple(self.sources, SourceModulationSource, "modulation sources")
        _require_enum_tuple(
            self.parameter_kinds,
            SourceModulationParameterKind,
            "modulation parameter_kinds",
        )
        _require_bool(self.inactive_readable, "modulation inactive_readable")


@dataclass(frozen=True, slots=True)
class SourceSweepCapabilityProfile:
    spacing_modes: tuple[SourceSweepSpacing, ...]
    trigger_sources: tuple[SourceTriggerSource, ...]
    timing_readable: bool
    marker_readable: bool

    def __post_init__(self) -> None:
        _require_enum_tuple(self.spacing_modes, SourceSweepSpacing, "sweep spacing_modes")
        _require_enum_tuple(self.trigger_sources, SourceTriggerSource, "sweep trigger_sources")
        _require_bool(self.timing_readable, "sweep timing_readable")
        _require_bool(self.marker_readable, "sweep marker_readable")


@dataclass(frozen=True, slots=True)
class SourceBurstCapabilityProfile:
    modes: tuple[SourceBurstMode, ...]
    trigger_sources: tuple[SourceTriggerSource, ...]
    timing_readable: bool
    gate_readable: bool

    def __post_init__(self) -> None:
        _require_enum_tuple(self.modes, SourceBurstMode, "burst modes")
        _require_enum_tuple(self.trigger_sources, SourceTriggerSource, "burst trigger_sources")
        _require_bool(self.timing_readable, "burst timing_readable")
        _require_bool(self.gate_readable, "burst gate_readable")


@dataclass(frozen=True, slots=True)
class SourcePulseCapabilityProfile:
    hold_modes: tuple[SourcePulseHoldBasis, ...]
    delay_readable: bool
    transitions_readable: bool

    def __post_init__(self) -> None:
        _require_enum_tuple(self.hold_modes, SourcePulseHoldBasis, "pulse hold_modes")
        _require_bool(self.delay_readable, "pulse delay_readable")
        _require_bool(self.transitions_readable, "pulse transitions_readable")


@dataclass(frozen=True, slots=True)
class SourceArbitraryCapabilityProfile:
    playback_modes: tuple[SourceArbitraryPlaybackMode, ...]
    selection_readable: bool
    storage_metadata_readable: bool
    sample_rate_readable: bool

    def __post_init__(self) -> None:
        _require_enum_tuple(
            self.playback_modes,
            SourceArbitraryPlaybackMode,
            "arbitrary playback_modes",
        )
        _require_bool(self.selection_readable, "arbitrary selection_readable")
        _require_bool(
            self.storage_metadata_readable,
            "arbitrary storage_metadata_readable",
        )
        _require_bool(self.sample_rate_readable, "arbitrary sample_rate_readable")


class SourceQueryEffect(StrEnum):
    PURE_READ = "pure_read"
    STATEFUL_CONSUMING_READ = "stateful_consuming_read"
    REQUIRES_SELECTOR_WRITE = "requires_selector_write"
    UNKNOWN_EFFECT = "unknown_effect"


@dataclass(frozen=True, slots=True)
class SourceCounterCapabilityProfile:
    input_ids: tuple[str, ...]
    measurement_kinds: tuple[SourceCounterMeasurementKind, ...]
    configuration_readable: bool
    query_effect: SourceQueryEffect

    def __post_init__(self) -> None:
        _require_token_tuple(self.input_ids, "counter input_ids", allow_empty=False)
        _require_enum_tuple(
            self.measurement_kinds,
            SourceCounterMeasurementKind,
            "counter measurement_kinds",
        )
        _require_bool(self.configuration_readable, "counter configuration_readable")
        if not isinstance(self.query_effect, SourceQueryEffect):
            raise ValueError("counter query_effect has an invalid type")


@dataclass(frozen=True, slots=True)
class SourceClockSyncCapabilityProfile:
    reference_clock_modes: tuple[SourceReferenceClockMode, ...]
    sync_readable: bool
    cascade_readable: bool

    def __post_init__(self) -> None:
        _require_enum_tuple(
            self.reference_clock_modes,
            SourceReferenceClockMode,
            "clock reference_clock_modes",
        )
        _require_bool(self.sync_readable, "clock sync_readable")
        _require_bool(self.cascade_readable, "clock cascade_readable")


@dataclass(frozen=True, slots=True)
class SourceCrossChannelCapabilityProfile:
    relation_kinds: tuple[SourceFeature, ...]
    supported_channel_sets: tuple[tuple[int, ...], ...]
    relation_graph_readable: bool
    shared_power_constraint_readable: bool

    def __post_init__(self) -> None:
        _require_enum_tuple(self.relation_kinds, SourceFeature, "cross-channel relation_kinds")
        allowed = {
            SourceFeature.COMBINE,
            SourceFeature.TRACKING,
            SourceFeature.COUPLING,
            SourceFeature.COPY,
            SourceFeature.PHASE_RELATION,
            SourceFeature.SHARED_POWER,
        }
        if not set(self.relation_kinds) <= allowed:
            raise ValueError("cross-channel relation_kinds contain a channel-local feature")
        if not isinstance(self.supported_channel_sets, tuple):
            raise ValueError("cross-channel supported_channel_sets must be a tuple")
        for channel_set in self.supported_channel_sets:
            _require_positive_channels(
                channel_set,
                "cross-channel supported channel set",
            )
            if len(channel_set) < 2:
                raise ValueError("cross-channel channel sets require at least two channels")
        if len(set(self.supported_channel_sets)) != len(self.supported_channel_sets):
            raise ValueError("cross-channel supported_channel_sets must be unique")
        _require_bool(self.relation_graph_readable, "cross-channel relation_graph_readable")
        _require_bool(
            self.shared_power_constraint_readable,
            "cross-channel shared_power_constraint_readable",
        )


SourceFeatureProfile: TypeAlias = (
    SourceBasicCapabilityProfile
    | SourceOutputCapabilityProfile
    | SourceHarmonicCapabilityProfile
    | SourceModulationCapabilityProfile
    | SourceSweepCapabilityProfile
    | SourceBurstCapabilityProfile
    | SourcePulseCapabilityProfile
    | SourceArbitraryCapabilityProfile
    | SourceCounterCapabilityProfile
    | SourceClockSyncCapabilityProfile
    | SourceCrossChannelCapabilityProfile
)


class SourceFacetScope(StrEnum):
    CHANNEL = "channel"
    CHANNEL_SET = "channel_set"
    INSTRUMENT = "instrument"
    INPUT = "input"


@dataclass(frozen=True, slots=True)
class SourceScopeRef:
    scope: SourceFacetScope
    channel: int | None = None
    channels: tuple[int, ...] = ()
    input_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, SourceFacetScope):
            raise ValueError("source scope has an invalid type")
        if self.scope is SourceFacetScope.CHANNEL:
            _require_int(self.channel, "source scope channel", minimum=1)
            if self.channels or self.input_id is not None:
                raise ValueError("CHANNEL scope can only carry channel")
        elif self.scope is SourceFacetScope.CHANNEL_SET:
            _require_positive_channels(self.channels, "source scope channels")
            if len(self.channels) < 2 or self.channel is not None or self.input_id is not None:
                raise ValueError("CHANNEL_SET scope requires only two or more channels")
        elif self.scope is SourceFacetScope.INPUT:
            _require_token(self.input_id, "source scope input_id")
            if self.channel is not None or self.channels:
                raise ValueError("INPUT scope can only carry input_id")
        elif self.channel is not None or self.channels or self.input_id is not None:
            raise ValueError("INSTRUMENT scope cannot carry channel or input fields")


@dataclass(frozen=True, slots=True)
class SourceTopologyContract:
    channels: tuple[int, ...]
    input_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_positive_channels(self.channels, "source topology channels")
        _require_token_tuple(self.input_ids, "source topology input_ids")


class SourceFieldId(StrEnum):
    IDENTITY = "source.identity"
    BASIC = "source.channel.basic"
    OUTPUT = "source.channel.output"
    DISPLAY_LOAD = "source.channel.display_load"
    HARMONICS = "source.channel.harmonics"
    MODULATION = "source.channel.modulation"
    SWEEP = "source.channel.sweep"
    BURST = "source.channel.burst"
    PULSE = "source.channel.pulse"
    ARBITRARY_SELECTION = "source.channel.arbitrary_selection"
    ARBITRARY_STORAGE = "source.channel.arbitrary_storage"
    ARM_STATE = "source.channel.arm_state"
    TRIGGER_STATE = "source.channel.trigger_state"
    COMBINE = "source.cross_channel.combine"
    COUPLING = "source.cross_channel.coupling"
    TRACKING = "source.cross_channel.tracking"
    COPY = "source.cross_channel.copy"
    PHASE_RELATION = "source.cross_channel.phase_relation"
    RELATION_GRAPH = "source.cross_channel.relation_graph"
    REFERENCE_CLOCK = "source.instrument.reference_clock"
    SYNC = "source.instrument.sync"
    CASCADE = "source.instrument.cascade"
    SHARED_POWER = "source.instrument.shared_power"
    COUNTER = "source.input.counter"


_FIELD_SCOPES: dict[SourceFieldId, frozenset[SourceFacetScope]] = {
    SourceFieldId.IDENTITY: frozenset({SourceFacetScope.INSTRUMENT}),
    SourceFieldId.BASIC: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.OUTPUT: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.DISPLAY_LOAD: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.HARMONICS: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.MODULATION: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.SWEEP: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.BURST: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.PULSE: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.ARBITRARY_SELECTION: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.ARBITRARY_STORAGE: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.ARM_STATE: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.TRIGGER_STATE: frozenset({SourceFacetScope.CHANNEL}),
    SourceFieldId.COMBINE: frozenset({SourceFacetScope.CHANNEL_SET}),
    SourceFieldId.COUPLING: frozenset({SourceFacetScope.CHANNEL_SET}),
    SourceFieldId.TRACKING: frozenset({SourceFacetScope.CHANNEL_SET}),
    SourceFieldId.COPY: frozenset({SourceFacetScope.CHANNEL_SET}),
    SourceFieldId.PHASE_RELATION: frozenset({SourceFacetScope.CHANNEL_SET}),
    SourceFieldId.RELATION_GRAPH: frozenset({SourceFacetScope.INSTRUMENT}),
    SourceFieldId.REFERENCE_CLOCK: frozenset({SourceFacetScope.INSTRUMENT}),
    SourceFieldId.SYNC: frozenset(
        {SourceFacetScope.INSTRUMENT, SourceFacetScope.CHANNEL_SET}
    ),
    SourceFieldId.CASCADE: frozenset(
        {SourceFacetScope.INSTRUMENT, SourceFacetScope.CHANNEL_SET}
    ),
    SourceFieldId.SHARED_POWER: frozenset({SourceFacetScope.INSTRUMENT}),
    SourceFieldId.COUNTER: frozenset({SourceFacetScope.INPUT}),
}


@dataclass(frozen=True, slots=True)
class SourceFieldRef:
    field: SourceFieldId
    target: SourceScopeRef

    def __post_init__(self) -> None:
        if not isinstance(self.field, SourceFieldId):
            raise ValueError("source field has an invalid type")
        if not isinstance(self.target, SourceScopeRef):
            raise ValueError("source field target has an invalid type")
        if self.target.scope not in _FIELD_SCOPES[self.field]:
            raise ValueError(
                f"source field {self.field.value!r} cannot use scope {self.target.scope.value!r}"
            )


class SourceEnergyEffect(StrEnum):
    NONE = "none"
    DECREASE_ONLY = "decrease_only"
    POTENTIAL_WHILE_OFF = "potential_while_off"
    MAY_INCREASE = "may_increase"
    EMIT = "emit"
    UNKNOWN = "unknown"


class SourceStorageEffect(StrEnum):
    NONE = "none"
    READ = "read"
    CREATE = "create"
    REPLACE = "replace"
    DELETE = "delete"
    UNKNOWN = "unknown"


class SourceV1WriteRouteId(StrEnum):
    SET_FREQUENCY = "source_service.set_frequency"
    SET_FUNCTION = "source_service.set_function"
    SET_AMPLITUDE_VPP = "source_service.set_amplitude_vpp"
    SET_SQUARE_DUTY_CYCLE = "source_service.set_square_duty_cycle"
    SET_OUTPUT = "source_service.set_output"
    CONFIGURE_COUPLING = "source_service.configure_coupling"
    CONFIGURE_HARMONICS = "source_service.configure_harmonics"
    CONFIGURE_AM = "source_service.configure_am_modulation"
    CONFIGURE_FM = "source_service.configure_fm_modulation"
    CONFIGURE_PM = "source_service.configure_pm_modulation"
    CONFIGURE_PWM = "source_service.configure_pwm_modulation"
    CONFIGURE_PULSE = "source_service.configure_pulse"
    CONFIGURE_BURST = "source_service.configure_burst"
    TRIGGER_BURST = "source_service.trigger_burst"
    CONFIGURE_SWEEP = "source_service.configure_sweep"
    TRIGGER_SWEEP = "source_service.trigger_sweep"
    UPLOAD_ARBITRARY = "source_service.upload_arbitrary_waveform"
    RESTORE = "source_service.restore_restorable_state"


def _source_scope_ref_sort_key(value: SourceScopeRef) -> tuple[object, ...]:
    return (
        value.scope.value,
        -1 if value.channel is None else value.channel,
        value.channels,
        "" if value.input_id is None else value.input_id,
    )


def _source_field_ref_sort_key(value: SourceFieldRef) -> tuple[object, ...]:
    return (value.field.value, *_source_scope_ref_sort_key(value.target))


def _require_source_field_ref_tuple(
    values: object,
    label: str,
    *,
    allow_empty: bool = False,
    sorted_values: bool = True,
) -> tuple[SourceFieldRef, ...]:
    if not isinstance(values, tuple) or any(not isinstance(item, SourceFieldRef) for item in values):
        raise ValueError(f"{label} must be a tuple of SourceFieldRef values")
    if not allow_empty and not values:
        raise ValueError(f"{label} must not be empty")
    keys = tuple(_source_field_ref_sort_key(item) for item in values)
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label} must be unique")
    if sorted_values and tuple(sorted(keys)) != keys:
        raise ValueError(f"{label} must be sorted")
    return values


def _require_source_channel_scope_tuple(
    values: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[SourceScopeRef, ...]:
    if not isinstance(values, tuple) or any(
        not isinstance(item, SourceScopeRef) or item.scope is not SourceFacetScope.CHANNEL
        for item in values
    ):
        raise ValueError(f"{label} must be a tuple of channel SourceScopeRef values")
    if not allow_empty and not values:
        raise ValueError(f"{label} must not be empty")
    keys = tuple(_source_scope_ref_sort_key(item) for item in values)
    if len(set(keys)) != len(keys) or tuple(sorted(keys)) != keys:
        raise ValueError(f"{label} must be sorted and unique")
    return values


@dataclass(frozen=True, slots=True)
class SourceOperationContract:
    """Static core contract for one future Source V2 mutation operation.

    Constructing the model never registers a capability.  Capability registry,
    driver Protocol, request/result model and Service entry must still be added
    together in the feature-specific implementation milestone.
    """

    operation: str
    capability: str
    feature: SourceFeature
    direction: SourceFeatureDirection
    energy_effect: SourceEnergyEffect
    storage_effect: SourceStorageEffect
    required_fields: tuple[SourceFieldId, ...]
    changed_fields: tuple[SourceFieldId, ...]
    postcondition_fields: tuple[SourceFieldId, ...]
    cleanup_verification_fields: tuple[SourceFieldId, ...]
    v1_equivalent_routes: tuple[SourceV1WriteRouteId, ...]
    v1_overlapping_routes: tuple[SourceV1WriteRouteId, ...]
    operation_timeout_ms: int
    main_max_steps: int
    recovery_max_steps: int
    verification_max_steps: int

    def __post_init__(self) -> None:
        _require_token(self.operation, "source operation contract operation")
        _require_token(self.capability, "source operation contract capability")
        if not self.operation.startswith("source.") or not self.capability.startswith("source."):
            raise ValueError("source operation contract operation and capability must use source.* IDs")
        if not isinstance(self.feature, SourceFeature):
            raise ValueError("source operation contract feature has an invalid type")
        if not isinstance(self.direction, SourceFeatureDirection):
            raise ValueError("source operation contract direction has an invalid type")
        if self.direction is SourceFeatureDirection.READ:
            raise ValueError("source operation contracts cannot use the read direction")
        if not isinstance(self.energy_effect, SourceEnergyEffect):
            raise ValueError("source operation contract energy_effect has an invalid type")
        if not isinstance(self.storage_effect, SourceStorageEffect):
            raise ValueError("source operation contract storage_effect has an invalid type")
        _require_enum_tuple(self.required_fields, SourceFieldId, "source operation required_fields")
        _require_enum_tuple(
            self.changed_fields,
            SourceFieldId,
            "source operation changed_fields",
            allow_empty=self.energy_effect is SourceEnergyEffect.NONE
            and self.storage_effect in {SourceStorageEffect.NONE, SourceStorageEffect.READ},
        )
        _require_enum_tuple(
            self.postcondition_fields,
            SourceFieldId,
            "source operation postcondition_fields",
            allow_empty=True,
        )
        _require_enum_tuple(
            self.cleanup_verification_fields,
            SourceFieldId,
            "source operation cleanup_verification_fields",
            allow_empty=True,
        )
        _require_enum_tuple(
            self.v1_equivalent_routes,
            SourceV1WriteRouteId,
            "source operation v1_equivalent_routes",
            allow_empty=True,
        )
        _require_enum_tuple(
            self.v1_overlapping_routes,
            SourceV1WriteRouteId,
            "source operation v1_overlapping_routes",
            allow_empty=True,
        )
        if set(self.v1_equivalent_routes) & set(self.v1_overlapping_routes):
            raise ValueError("source operation V1 route groups must not overlap")
        for label, value in (
            ("source operation operation_timeout_ms", self.operation_timeout_ms),
            ("source operation main_max_steps", self.main_max_steps),
            ("source operation recovery_max_steps", self.recovery_max_steps),
            ("source operation verification_max_steps", self.verification_max_steps),
        ):
            _require_int(value, label, minimum=1)


SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT = SourceOperationContract(
    operation="source.basic_configure_v2",
    capability="source.basic_configure_v2",
    feature=SourceFeature.BASIC,
    direction=SourceFeatureDirection.CONFIGURE,
    energy_effect=SourceEnergyEffect.POTENTIAL_WHILE_OFF,
    storage_effect=SourceStorageEffect.NONE,
    required_fields=(
        SourceFieldId.BASIC,
        SourceFieldId.OUTPUT,
        SourceFieldId.IDENTITY,
    ),
    changed_fields=(SourceFieldId.BASIC,),
    postcondition_fields=(SourceFieldId.BASIC,),
    cleanup_verification_fields=(
        SourceFieldId.BASIC,
        SourceFieldId.OUTPUT,
    ),
    v1_equivalent_routes=(
        SourceV1WriteRouteId.SET_AMPLITUDE_VPP,
        SourceV1WriteRouteId.SET_FREQUENCY,
        SourceV1WriteRouteId.SET_FUNCTION,
        SourceV1WriteRouteId.SET_SQUARE_DUTY_CYCLE,
    ),
    v1_overlapping_routes=(
        SourceV1WriteRouteId.RESTORE,
        SourceV1WriteRouteId.UPLOAD_ARBITRARY,
    ),
    operation_timeout_ms=5_000,
    main_max_steps=1,
    recovery_max_steps=2,
    verification_max_steps=2,
)


SOURCE_HARMONICS_CONFIGURE_V2_OPERATION_CONTRACT = SourceOperationContract(
    operation="source.harmonics_configure_v2",
    capability="source.harmonics_configure_v2",
    feature=SourceFeature.HARMONICS,
    direction=SourceFeatureDirection.CONFIGURE,
    energy_effect=SourceEnergyEffect.POTENTIAL_WHILE_OFF,
    storage_effect=SourceStorageEffect.NONE,
    required_fields=(
        SourceFieldId.HARMONICS,
        SourceFieldId.OUTPUT,
        SourceFieldId.IDENTITY,
    ),
    changed_fields=(SourceFieldId.HARMONICS,),
    postcondition_fields=(
        SourceFieldId.HARMONICS,
        SourceFieldId.OUTPUT,
    ),
    cleanup_verification_fields=(SourceFieldId.OUTPUT,),
    v1_equivalent_routes=(SourceV1WriteRouteId.CONFIGURE_HARMONICS,),
    v1_overlapping_routes=(SourceV1WriteRouteId.RESTORE,),
    operation_timeout_ms=5_000,
    main_max_steps=1,
    recovery_max_steps=1,
    verification_max_steps=2,
)


SOURCE_OUTPUT_ENABLE_V2_OPERATION_CONTRACT = SourceOperationContract(
    operation="source.output_enable_v2",
    capability="source.output_v2",
    feature=SourceFeature.OUTPUT,
    direction=SourceFeatureDirection.ENABLE,
    energy_effect=SourceEnergyEffect.EMIT,
    storage_effect=SourceStorageEffect.NONE,
    required_fields=(
        SourceFieldId.BASIC,
        SourceFieldId.OUTPUT,
        SourceFieldId.IDENTITY,
    ),
    changed_fields=(SourceFieldId.OUTPUT,),
    postcondition_fields=(
        SourceFieldId.BASIC,
        SourceFieldId.OUTPUT,
    ),
    cleanup_verification_fields=(SourceFieldId.OUTPUT,),
    v1_equivalent_routes=(SourceV1WriteRouteId.SET_OUTPUT,),
    v1_overlapping_routes=(
        SourceV1WriteRouteId.RESTORE,
        SourceV1WriteRouteId.TRIGGER_BURST,
        SourceV1WriteRouteId.TRIGGER_SWEEP,
        SourceV1WriteRouteId.UPLOAD_ARBITRARY,
    ),
    operation_timeout_ms=5_000,
    main_max_steps=1,
    recovery_max_steps=1,
    verification_max_steps=1,
)


SOURCE_OUTPUT_DISABLE_V2_OPERATION_CONTRACT = SourceOperationContract(
    operation="source.output_disable_v2",
    capability="source.output_v2",
    feature=SourceFeature.OUTPUT,
    direction=SourceFeatureDirection.DISABLE,
    energy_effect=SourceEnergyEffect.DECREASE_ONLY,
    storage_effect=SourceStorageEffect.NONE,
    required_fields=(SourceFieldId.OUTPUT,),
    changed_fields=(SourceFieldId.OUTPUT,),
    postcondition_fields=(SourceFieldId.OUTPUT,),
    cleanup_verification_fields=(SourceFieldId.OUTPUT,),
    v1_equivalent_routes=(SourceV1WriteRouteId.SET_OUTPUT,),
    v1_overlapping_routes=(SourceV1WriteRouteId.RESTORE,),
    operation_timeout_ms=5_000,
    main_max_steps=1,
    recovery_max_steps=1,
    verification_max_steps=1,
)


@dataclass(frozen=True, slots=True)
class SourceAffectedClosure:
    """A core-created, context-bound field closure for one Source operation."""

    operation: str
    context_id: str
    session_epoch: str
    baseline_snapshot_digest: str
    fields: tuple[SourceFieldRef, ...]
    required_off_outputs: tuple[SourceScopeRef, ...]
    emergency_off_outputs: tuple[SourceScopeRef, ...]
    restore_order: tuple[SourceFieldRef, ...]
    non_restorable_fields: tuple[SourceFieldRef, ...]
    closure_digest: str

    def __post_init__(self) -> None:
        _require_token(self.operation, "source affected closure operation")
        _require_token(self.context_id, "source affected closure context_id")
        _require_token(self.session_epoch, "source affected closure session_epoch")
        for label, value in (
            ("source affected closure baseline_snapshot_digest", self.baseline_snapshot_digest),
            ("source affected closure closure_digest", self.closure_digest),
        ):
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
        fields = _require_source_field_ref_tuple(self.fields, "source affected closure fields")
        required_off = _require_source_channel_scope_tuple(
            self.required_off_outputs,
            "source affected closure required_off_outputs",
            allow_empty=True,
        )
        emergency_off = _require_source_channel_scope_tuple(
            self.emergency_off_outputs,
            "source affected closure emergency_off_outputs",
            allow_empty=True,
        )
        restore_order = _require_source_field_ref_tuple(
            self.restore_order,
            "source affected closure restore_order",
            allow_empty=True,
            sorted_values=False,
        )
        non_restorable = _require_source_field_ref_tuple(
            self.non_restorable_fields,
            "source affected closure non_restorable_fields",
            allow_empty=True,
        )
        if not set(required_off) <= set(emergency_off):
            raise ValueError("source affected closure emergency OFF set must cover required OFF outputs")
        if not set(restore_order) <= set(fields):
            raise ValueError("source affected closure restore_order exceeds fields")
        if not set(non_restorable) <= set(fields):
            raise ValueError("source affected closure non_restorable_fields exceeds fields")
        if set(restore_order) & set(non_restorable):
            raise ValueError("source affected closure restore and non-restorable fields overlap")
        expected = source_v2_digest(
            {
                "schema": "wavebench.source.affected-closure.v1",
                "operation": self.operation,
                "context_id": self.context_id,
                "session_epoch": self.session_epoch,
                "baseline_snapshot_digest": self.baseline_snapshot_digest,
                "fields": fields,
                "required_off_outputs": required_off,
                "emergency_off_outputs": emergency_off,
                "restore_order": restore_order,
                "non_restorable_fields": non_restorable,
            }
        )
        if self.closure_digest != expected:
            raise ValueError("source affected closure digest does not match its fields")


class SourceSignalPathKind(StrEnum):
    INTERNAL_WAVEFORM = "internal_waveform"
    OUTPUT_PORT = "output_port"
    CONFIG_TRACKING = "config_tracking"
    SHARED_RESOURCE = "shared_resource"


@dataclass(frozen=True, slots=True)
class SourceRelationEdge:
    relation_id: str
    feature: SourceFeature
    sources: tuple[int, ...]
    targets: tuple[int, ...]
    signal_path: SourceSignalPathKind
    affected_fields: tuple[SourceFieldId, ...]
    implicit_changed_fields: tuple[SourceFieldId, ...] = ()

    def __post_init__(self) -> None:
        _require_token(self.relation_id, "source relation_id")
        if self.feature not in {
            SourceFeature.COMBINE,
            SourceFeature.TRACKING,
            SourceFeature.COUPLING,
            SourceFeature.COPY,
            SourceFeature.PHASE_RELATION,
            SourceFeature.SHARED_POWER,
        }:
            raise ValueError("source relation feature is not cross-channel")
        _require_positive_channels(self.sources, "source relation sources")
        _require_positive_channels(self.targets, "source relation targets")
        if set(self.sources) & set(self.targets):
            raise ValueError("source relation sources and targets must be disjoint")
        if not isinstance(self.signal_path, SourceSignalPathKind):
            raise ValueError("source relation signal_path has an invalid type")
        _require_enum_tuple(
            self.affected_fields,
            SourceFieldId,
            "source relation affected_fields",
        )
        _require_enum_tuple(
            self.implicit_changed_fields,
            SourceFieldId,
            "source relation implicit_changed_fields",
            allow_empty=True,
        )


@dataclass(frozen=True, slots=True)
class SourceRelationGraph:
    channels: tuple[int, ...]
    edges: tuple[SourceRelationEdge, ...]

    def __post_init__(self) -> None:
        _require_positive_channels(self.channels, "source relation graph channels")
        if not isinstance(self.edges, tuple) or any(
            not isinstance(edge, SourceRelationEdge) for edge in self.edges
        ):
            raise ValueError("source relation graph edges have an invalid type")
        relation_ids = tuple(edge.relation_id for edge in self.edges)
        if len(set(relation_ids)) != len(relation_ids) or tuple(sorted(relation_ids)) != relation_ids:
            raise ValueError("source relation graph edges must be sorted by relation_id and unique")
        participants = set(self.channels)
        if any(not set(edge.sources + edge.targets) <= participants for edge in self.edges):
            raise ValueError("source relation graph edge references an unknown channel")
        adjacency: dict[int, set[int]] = {channel: set() for channel in self.channels}
        for edge in self.edges:
            for source in edge.sources:
                adjacency[source].update(edge.targets)

        def visit(channel: int, visiting: set[int], visited: set[int]) -> None:
            if channel in visiting:
                raise ValueError("source relation graph cannot contain directed cycles")
            if channel in visited:
                return
            visiting.add(channel)
            for target in adjacency[channel]:
                visit(target, visiting, visited)
            visiting.remove(channel)
            visited.add(channel)

        visited: set[int] = set()
        for channel in self.channels:
            visit(channel, set(), visited)


class SourceAnchorField(StrEnum):
    WAVEFORM_KIND = "waveform_kind"
    FREQUENCY_MODE = "frequency_mode"
    OUTPUT_ENABLED = "output_enabled"
    HARMONICS_ENABLED = "harmonics_enabled"
    MODULATION_ENABLED = "modulation_enabled"
    SWEEP_ENABLED = "sweep_enabled"
    BURST_ENABLED = "burst_enabled"
    ARBITRARY_PLAYBACK_MODE = "arbitrary_playback_mode"
    COMBINE_ENABLED = "combine_enabled"
    COUPLING_ENABLED = "coupling_enabled"
    TRACKING_ENABLED = "tracking_enabled"


SourceAnchorValue: TypeAlias = (
    bool | SourceWaveformKind | SourceFrequencyMode | SourceArbitraryPlaybackMode
)


@dataclass(frozen=True, slots=True)
class SourceActivationPredicate:
    field: SourceAnchorField
    equals: SourceAnchorValue

    def __post_init__(self) -> None:
        if not isinstance(self.field, SourceAnchorField):
            raise ValueError("source activation field has an invalid type")
        if isinstance(self.equals, bool):
            return
        if not isinstance(
            self.equals,
            (SourceWaveformKind, SourceFrequencyMode, SourceArbitraryPlaybackMode),
        ):
            raise ValueError("source activation value has an invalid type")


@dataclass(frozen=True, slots=True)
class SourceActivationRule:
    predicates: tuple[SourceActivationPredicate, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.predicates, tuple) or not self.predicates:
            raise ValueError("source activation rule requires predicates")
        if any(not isinstance(item, SourceActivationPredicate) for item in self.predicates):
            raise ValueError("source activation predicates have an invalid type")
        keys = tuple(item.field.value for item in self.predicates)
        if len(set(keys)) != len(keys) or tuple(sorted(keys)) != keys:
            raise ValueError("source activation predicates must be sorted by field and unique")


@dataclass(frozen=True, slots=True)
class ClosedFloatInterval:
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        _require_finite(self.minimum, "interval minimum")
        _require_finite(self.maximum, "interval maximum")
        if self.minimum > self.maximum:
            raise ValueError("interval minimum must not exceed maximum")


@dataclass(frozen=True, slots=True)
class SourceConstraintApplicability:
    models: tuple[str, ...] = ()
    firmware_ids: tuple[str, ...] = ()
    option_ids: tuple[str, ...] = ()
    waveform_kinds: tuple[SourceWaveformKind, ...] = ()
    frequency_hz: ClosedFloatInterval | None = None
    amplitude_vpp: ClosedFloatInterval | None = None
    offset_v: ClosedFloatInterval | None = None

    def __post_init__(self) -> None:
        _require_text_tuple(self.models, "applicability models")
        _require_text_tuple(self.firmware_ids, "applicability firmware_ids")
        _require_token_tuple(self.option_ids, "applicability option_ids")
        _require_enum_tuple(
            self.waveform_kinds,
            SourceWaveformKind,
            "applicability waveform_kinds",
            allow_empty=True,
        )
        for name, value in (
            ("frequency_hz", self.frequency_hz),
            ("amplitude_vpp", self.amplitude_vpp),
            ("offset_v", self.offset_v),
        ):
            if value is not None and not isinstance(value, ClosedFloatInterval):
                raise ValueError(f"applicability {name} has an invalid type")


@dataclass(frozen=True, slots=True)
class SourceRuntimeIdentity:
    manufacturer: str
    model: str
    firmware_id: str
    option_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.manufacturer, "source runtime manufacturer")
        _require_text(self.model, "source runtime model")
        _require_text(self.firmware_id, "source runtime firmware_id")
        _require_token_tuple(self.option_ids, "source runtime option_ids")


_FEATURE_PROFILE_TYPES: dict[SourceFeature, type[object]] = {
    SourceFeature.BASIC: SourceBasicCapabilityProfile,
    SourceFeature.OUTPUT: SourceOutputCapabilityProfile,
    SourceFeature.HARMONICS: SourceHarmonicCapabilityProfile,
    SourceFeature.MODULATION: SourceModulationCapabilityProfile,
    SourceFeature.SWEEP: SourceSweepCapabilityProfile,
    SourceFeature.BURST: SourceBurstCapabilityProfile,
    SourceFeature.PULSE: SourcePulseCapabilityProfile,
    SourceFeature.ARBITRARY: SourceArbitraryCapabilityProfile,
    SourceFeature.COUNTER: SourceCounterCapabilityProfile,
    SourceFeature.REFERENCE_CLOCK: SourceClockSyncCapabilityProfile,
    SourceFeature.SYNC: SourceClockSyncCapabilityProfile,
    SourceFeature.CASCADE: SourceClockSyncCapabilityProfile,
    SourceFeature.COMBINE: SourceCrossChannelCapabilityProfile,
    SourceFeature.TRACKING: SourceCrossChannelCapabilityProfile,
    SourceFeature.COUPLING: SourceCrossChannelCapabilityProfile,
    SourceFeature.COPY: SourceCrossChannelCapabilityProfile,
    SourceFeature.PHASE_RELATION: SourceCrossChannelCapabilityProfile,
    SourceFeature.SHARED_POWER: SourceCrossChannelCapabilityProfile,
}

_FEATURE_SCOPES: dict[SourceFeature, frozenset[SourceFacetScope]] = {
    SourceFeature.BASIC: frozenset({SourceFacetScope.CHANNEL}),
    SourceFeature.OUTPUT: frozenset({SourceFacetScope.CHANNEL}),
    SourceFeature.HARMONICS: frozenset({SourceFacetScope.CHANNEL}),
    SourceFeature.MODULATION: frozenset({SourceFacetScope.CHANNEL}),
    SourceFeature.SWEEP: frozenset({SourceFacetScope.CHANNEL}),
    SourceFeature.BURST: frozenset({SourceFacetScope.CHANNEL}),
    SourceFeature.PULSE: frozenset({SourceFacetScope.CHANNEL}),
    SourceFeature.ARBITRARY: frozenset({SourceFacetScope.CHANNEL}),
    SourceFeature.COUNTER: frozenset({SourceFacetScope.INPUT}),
    SourceFeature.REFERENCE_CLOCK: frozenset({SourceFacetScope.INSTRUMENT}),
    SourceFeature.SYNC: frozenset(
        {SourceFacetScope.INSTRUMENT, SourceFacetScope.CHANNEL_SET}
    ),
    SourceFeature.CASCADE: frozenset(
        {SourceFacetScope.INSTRUMENT, SourceFacetScope.CHANNEL_SET}
    ),
    SourceFeature.COMBINE: frozenset({SourceFacetScope.CHANNEL_SET}),
    SourceFeature.TRACKING: frozenset({SourceFacetScope.CHANNEL_SET}),
    SourceFeature.COUPLING: frozenset({SourceFacetScope.CHANNEL_SET}),
    SourceFeature.COPY: frozenset({SourceFacetScope.CHANNEL_SET}),
    SourceFeature.PHASE_RELATION: frozenset({SourceFacetScope.CHANNEL_SET}),
    SourceFeature.SHARED_POWER: frozenset({SourceFacetScope.INSTRUMENT}),
}


@dataclass(frozen=True, slots=True)
class SourceFeatureCapability:
    feature: SourceFeature
    support: SupportState
    directions: tuple[SourceFeatureDirection, ...]
    scope: SourceFacetScope
    channels: tuple[int, ...]
    applicability: SourceConstraintApplicability
    profile: SourceFeatureProfile
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.feature, SourceFeature):
            raise ValueError("source feature has an invalid type")
        if not isinstance(self.support, SupportState):
            raise ValueError("source support state has an invalid type")
        _require_enum_tuple(
            self.directions,
            SourceFeatureDirection,
            "source directions",
            allow_empty=self.support is not SupportState.SUPPORTED,
        )
        if not isinstance(self.scope, SourceFacetScope):
            raise ValueError("source feature scope has an invalid type")
        if self.scope not in _FEATURE_SCOPES[self.feature]:
            raise ValueError(
                f"source feature {self.feature.value!r} cannot use scope {self.scope.value!r}"
            )
        _require_positive_channels(
            self.channels,
            "source feature channels",
            allow_empty=self.scope in {SourceFacetScope.INSTRUMENT, SourceFacetScope.INPUT},
        )
        if self.scope is SourceFacetScope.CHANNEL and not self.channels:
            raise ValueError("channel-scoped source features require channels")
        if self.scope is SourceFacetScope.CHANNEL_SET and len(self.channels) < 2:
            raise ValueError("channel-set source features require two or more channels")
        if self.scope in {SourceFacetScope.INSTRUMENT, SourceFacetScope.INPUT} and self.channels:
            raise ValueError("instrument/input source features cannot carry channels")
        if not isinstance(self.applicability, SourceConstraintApplicability):
            raise ValueError("source feature applicability has an invalid type")
        expected = _FEATURE_PROFILE_TYPES[self.feature]
        if not isinstance(self.profile, expected):
            raise ValueError(
                f"source feature {self.feature.value!r} requires {expected.__name__}"
            )
        _require_evidence_ref_tuple(self.evidence_refs, "source feature evidence_refs")


class BudgetProofStrength(StrEnum):
    HARD_CONSERVATIVE = "hard_conservative"
    STATISTICAL_ONLY = "statistical_only"
    MEASURED_ONLY = "measured_only"
    INCOMPLETE = "incomplete"


class SourceSafetyConstraintKind(StrEnum):
    VOLTAGE_REFERENCE = "voltage_reference"
    SOURCE_RESISTANCE = "source_resistance"
    FREQUENCY_DERATING = "frequency_derating"
    MODULATION_ENVELOPE = "modulation_envelope"
    ARBITRARY_OVERSHOOT = "arbitrary_overshoot"
    NOISE_PEAK = "noise_peak"
    SHARED_POWER = "shared_power"


class VoltageReferenceBasis(StrEnum):
    OPEN_CIRCUIT = "open_circuit"
    DELIVERED_INTO_DISPLAY_LOAD = "delivered_into_display_load"


class TerminationKind(StrEnum):
    HIGH_IMPEDANCE = "high_impedance"
    RESISTIVE = "resistive"


@dataclass(frozen=True, slots=True)
class ResistanceBounds:
    minimum_ohm: float
    maximum_ohm: float

    def __post_init__(self) -> None:
        _require_finite(self.minimum_ohm, "resistance minimum_ohm", minimum=0.0)
        _require_finite(self.maximum_ohm, "resistance maximum_ohm", minimum=self.minimum_ohm)
        if self.minimum_ohm == 0:
            raise ValueError("resistance minimum_ohm must be positive")


@dataclass(frozen=True, slots=True)
class TerminationSpec:
    kind: TerminationKind
    resistance_bounds: ResistanceBounds | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TerminationKind):
            raise ValueError("termination kind has an invalid type")
        if self.resistance_bounds is not None and not isinstance(
            self.resistance_bounds,
            ResistanceBounds,
        ):
            raise ValueError("termination resistance_bounds has an invalid type")
        if self.kind is TerminationKind.RESISTIVE and self.resistance_bounds is None:
            raise ValueError("resistive termination requires resistance_bounds")


class TerminationEvidenceSource(StrEnum):
    CONFIG = "config"
    RUN_INTENT = "run_intent"
    MANUAL_CONFIRMATION = "manual_confirmation"
    EXTERNAL_MEASUREMENT = "external_measurement"


class TerminationEvidenceLifetime(StrEnum):
    OPERATION = "operation"
    RUN = "run"
    CONFIG_DIGEST = "config_digest"


class BudgetEvidenceSource(StrEnum):
    INSTRUMENT_READBACK = "instrument_readback"
    DEVICE_HARD_LIMIT = "device_hard_limit"
    EXPLICIT_TERMINATION = "explicit_termination"
    EXTERNAL_MEASUREMENT = "external_measurement"


class SourceBudgetBlockerCode(StrEnum):
    SNAPSHOT_NOT_CONSISTENT = "snapshot_not_consistent"
    DESCRIPTOR_MISMATCH = "descriptor_mismatch"
    TARGET_CHANNEL_UNKNOWN = "target_channel_unknown"
    BASIC_STATE_UNAVAILABLE = "basic_state_unavailable"
    AMPLITUDE_UNIT_UNSUPPORTED = "amplitude_unit_unsupported"
    WAVEFORM_UNSUPPORTED = "waveform_unsupported"
    DC_LEVEL_UNAVAILABLE = "dc_level_unavailable"
    FREQUENCY_MODE_UNSUPPORTED = "frequency_mode_unsupported"
    OUTPUT_POLARITY_UNAVAILABLE = "output_polarity_unavailable"
    VOLTAGE_REFERENCE_MISSING = "voltage_reference_missing"
    SOURCE_RESISTANCE_MISSING = "source_resistance_missing"
    DISPLAY_LOAD_UNAVAILABLE = "display_load_unavailable"
    DISPLAY_LOAD_UNSUPPORTED = "display_load_unsupported"
    ACTUAL_TERMINATION_MISSING = "actual_termination_missing"
    TERMINATION_EVIDENCE_INVALID = "termination_evidence_invalid"
    TERMINATION_NOT_RESISTIVE = "termination_not_resistive"
    HARMONIC_STATE_UNAVAILABLE = "harmonic_state_unavailable"
    HARMONIC_COMPLETENESS_INSUFFICIENT = "harmonic_completeness_insufficient"
    HARMONIC_AMPLITUDE_UNSUPPORTED = "harmonic_amplitude_unsupported"
    MODULATION_CONSTRAINT_MISSING = "modulation_constraint_missing"
    ARBITRARY_OVERSHOOT_MISSING = "arbitrary_overshoot_missing"
    NOISE_PEAK_MISSING = "noise_peak_missing"
    SWEEP_DERATING_MISSING = "sweep_derating_missing"
    ACTIVE_CHANNEL_UNKNOWN = "active_channel_unknown"
    COMBINE_STATE_UNAVAILABLE = "combine_state_unavailable"
    COMBINE_PATH_UNSUPPORTED = "combine_path_unsupported"
    SHARED_POWER_STATE_UNAVAILABLE = "shared_power_state_unavailable"
    SHARED_POWER_CONSTRAINT_MISSING = "shared_power_constraint_missing"
    SHARED_POWER_LIMIT_EXCEEDED = "shared_power_limit_exceeded"
    CONSTRAINT_NOT_HARD = "constraint_not_hard"
    VPP_LIMIT_EXCEEDED = "vpp_limit_exceeded"
    PORT_VOLTAGE_LIMIT_EXCEEDED = "port_voltage_limit_exceeded"


@dataclass(frozen=True, slots=True)
class PortVoltageBounds:
    minimum_v_lower: float
    maximum_v_upper: float
    vpp_upper_v: float
    absolute_peak_upper_v: float
    rms_upper_v: float | None

    def __post_init__(self) -> None:
        _require_finite(self.minimum_v_lower, "port voltage minimum_v_lower")
        _require_finite(self.maximum_v_upper, "port voltage maximum_v_upper")
        if self.minimum_v_lower > self.maximum_v_upper:
            raise ValueError("port voltage minimum_v_lower must not exceed maximum_v_upper")
        _require_finite(self.vpp_upper_v, "port voltage vpp_upper_v", minimum=0.0)
        _require_finite(
            self.absolute_peak_upper_v,
            "port voltage absolute_peak_upper_v",
            minimum=0.0,
        )
        span = self.maximum_v_upper - self.minimum_v_lower
        if self.vpp_upper_v < span:
            raise ValueError("port voltage vpp_upper_v must cover the voltage span")
        if self.absolute_peak_upper_v < max(
            abs(self.minimum_v_lower),
            abs(self.maximum_v_upper),
        ):
            raise ValueError("port voltage absolute_peak_upper_v must cover both bounds")
        if self.rms_upper_v is not None:
            _require_finite(self.rms_upper_v, "port voltage rms_upper_v", minimum=0.0)
            if self.rms_upper_v > self.absolute_peak_upper_v:
                raise ValueError("port voltage rms_upper_v cannot exceed absolute_peak_upper_v")


@dataclass(frozen=True, slots=True)
class SafetyContributor:
    contributor_id: str
    feature: SourceFeature
    channels: tuple[int, ...]
    minimum_v: float
    maximum_v: float
    constraint_ids: tuple[str, ...]
    proof_strength: BudgetProofStrength
    evidence_sources: tuple[BudgetEvidenceSource, ...]

    def __post_init__(self) -> None:
        _require_token(self.contributor_id, "safety contributor_id")
        if not isinstance(self.feature, SourceFeature):
            raise ValueError("safety contributor feature has an invalid type")
        _require_positive_channels(self.channels, "safety contributor channels")
        _require_finite(self.minimum_v, "safety contributor minimum_v")
        _require_finite(self.maximum_v, "safety contributor maximum_v")
        if self.minimum_v > self.maximum_v:
            raise ValueError("safety contributor minimum_v must not exceed maximum_v")
        _require_token_tuple(self.constraint_ids, "safety contributor constraint_ids")
        if not isinstance(self.proof_strength, BudgetProofStrength):
            raise ValueError("safety contributor proof_strength has an invalid type")
        _require_enum_tuple(
            self.evidence_sources,
            BudgetEvidenceSource,
            "safety contributor evidence_sources",
            allow_empty=True,
        )


@dataclass(frozen=True, slots=True)
class SourceSharedPowerBudget:
    """Auditable shared-power envelope used by a composite output budget.

    ``observed_active_power_upper_w`` comes from the current snapshot.  The
    projected bound independently includes the requested target output and
    every explicitly active direct output, so an ON preflight cannot rely on a
    current-reading value that excludes the target while it is still OFF.
    """

    participants: tuple[int, ...]
    observed_active_power_upper_w: float
    projected_power_upper_w: float
    effective_hard_limit_w: float
    constraint_ids: tuple[str, ...]
    evidence_sources: tuple[BudgetEvidenceSource, ...]

    def __post_init__(self) -> None:
        _require_positive_channels(self.participants, "shared power budget participants")
        for label, value in (
            ("shared power budget observed_active_power_upper_w", self.observed_active_power_upper_w),
            ("shared power budget projected_power_upper_w", self.projected_power_upper_w),
            ("shared power budget effective_hard_limit_w", self.effective_hard_limit_w),
        ):
            _require_finite(value, label, minimum=0.0)
        _require_token_tuple(self.constraint_ids, "shared power budget constraint_ids")
        _require_enum_tuple(
            self.evidence_sources,
            BudgetEvidenceSource,
            "shared power budget evidence_sources",
        )


@dataclass(frozen=True, slots=True)
class CompositeOutputBudget:
    bounds: Observed[PortVoltageBounds]
    voltage_reference_basis: Observed[VoltageReferenceBasis]
    display_load: Observed[TerminationSpec]
    output_source_resistance: Observed[ResistanceBounds]
    actual_termination: Observed[TerminationSpec]
    shared_power: Observed[SourceSharedPowerBudget]
    proof_strength: BudgetProofStrength
    evidence_sources: tuple[BudgetEvidenceSource, ...]
    contributors: tuple[SafetyContributor, ...]
    blockers: tuple[SourceBudgetBlockerCode, ...]

    def __post_init__(self) -> None:
        for label, value in (
            ("budget bounds", self.bounds),
            ("budget voltage_reference_basis", self.voltage_reference_basis),
            ("budget display_load", self.display_load),
            ("budget output_source_resistance", self.output_source_resistance),
            ("budget actual_termination", self.actual_termination),
            ("budget shared_power", self.shared_power),
        ):
            _require_observed(value, label)
        if self.bounds.availability is Availability.VALUE and not isinstance(
            self.bounds.value,
            PortVoltageBounds,
        ):
            raise ValueError("budget bounds value has an invalid type")
        if self.shared_power.availability is Availability.VALUE and not isinstance(
            self.shared_power.value,
            SourceSharedPowerBudget,
        ):
            raise ValueError("budget shared_power value has an invalid type")
        if not isinstance(self.proof_strength, BudgetProofStrength):
            raise ValueError("budget proof_strength has an invalid type")
        _require_enum_tuple(
            self.evidence_sources,
            BudgetEvidenceSource,
            "budget evidence_sources",
            allow_empty=True,
        )
        if not isinstance(self.contributors, tuple) or any(
            not isinstance(item, SafetyContributor) for item in self.contributors
        ):
            raise ValueError("budget contributors have an invalid type")
        contributor_ids = tuple(item.contributor_id for item in self.contributors)
        if len(set(contributor_ids)) != len(contributor_ids) or tuple(
            sorted(contributor_ids)
        ) != contributor_ids:
            raise ValueError("budget contributors must be sorted by contributor_id and unique")
        _require_enum_tuple(
            self.blockers,
            SourceBudgetBlockerCode,
            "budget blockers",
            allow_empty=True,
        )
        if self.proof_strength is BudgetProofStrength.HARD_CONSERVATIVE and (
            self.blockers or self.bounds.availability is not Availability.VALUE
        ):
            raise ValueError("hard conservative budgets require bounds and no blockers")

    @property
    def can_authorize_energy(self) -> bool:
        return (
            self.proof_strength is BudgetProofStrength.HARD_CONSERVATIVE
            and not self.blockers
            and self.bounds.availability is Availability.VALUE
        )


_TERMINATION_EVIDENCE_LIFETIMES: dict[
    TerminationEvidenceSource,
    frozenset[TerminationEvidenceLifetime],
] = {
    TerminationEvidenceSource.CONFIG: frozenset({TerminationEvidenceLifetime.CONFIG_DIGEST}),
    TerminationEvidenceSource.RUN_INTENT: frozenset({TerminationEvidenceLifetime.RUN}),
    TerminationEvidenceSource.MANUAL_CONFIRMATION: frozenset(
        {TerminationEvidenceLifetime.OPERATION}
    ),
    TerminationEvidenceSource.EXTERNAL_MEASUREMENT: frozenset(
        {
            TerminationEvidenceLifetime.OPERATION,
            TerminationEvidenceLifetime.RUN,
            TerminationEvidenceLifetime.CONFIG_DIGEST,
        }
    ),
}


def _parse_utc_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be an RFC 3339 UTC timestamp") from exc


@dataclass(frozen=True, slots=True)
class SourceTerminationEvidence:
    target: SourceScopeRef
    termination: TerminationSpec
    source: TerminationEvidenceSource
    lifetime: TerminationEvidenceLifetime
    resource_fingerprint: str
    binding_digest: str
    observed_at_utc: str
    expires_at_utc: str | None
    evidence_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, SourceScopeRef) or self.target.scope is not SourceFacetScope.CHANNEL:
            raise ValueError("termination evidence target must be a channel scope")
        if not isinstance(self.termination, TerminationSpec):
            raise ValueError("termination evidence has an invalid termination")
        if not isinstance(self.source, TerminationEvidenceSource):
            raise ValueError("termination evidence source has an invalid type")
        if not isinstance(self.lifetime, TerminationEvidenceLifetime):
            raise ValueError("termination evidence lifetime has an invalid type")
        if self.lifetime not in _TERMINATION_EVIDENCE_LIFETIMES[self.source]:
            raise ValueError("termination evidence source and lifetime are incompatible")
        for label, value in (
            ("termination evidence resource_fingerprint", self.resource_fingerprint),
            ("termination evidence binding_digest", self.binding_digest),
        ):
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
        observed_at = _parse_utc_timestamp(
            self.observed_at_utc,
            "termination evidence observed_at_utc",
        )
        if self.expires_at_utc is not None:
            expires_at = _parse_utc_timestamp(
                self.expires_at_utc,
                "termination evidence expires_at_utc",
            )
            if expires_at <= observed_at:
                raise ValueError("termination evidence expires_at_utc must be after observed_at_utc")
        _require_evidence_ref_tuple((self.evidence_ref,), "termination evidence evidence_ref")


@dataclass(frozen=True, slots=True)
class SourceVoltageReferenceConstraint:
    basis: VoltageReferenceBasis

    def __post_init__(self) -> None:
        if not isinstance(self.basis, VoltageReferenceBasis):
            raise ValueError("voltage reference basis has an invalid type")


@dataclass(frozen=True, slots=True)
class SourceResistanceConstraint:
    resistance_ohm: ResistanceBounds

    def __post_init__(self) -> None:
        if not isinstance(self.resistance_ohm, ResistanceBounds):
            raise ValueError("source resistance constraint has an invalid type")


@dataclass(frozen=True, slots=True)
class SourceFrequencyDeratingBand:
    frequency_hz: ClosedFloatInterval
    gain_upper: float

    def __post_init__(self) -> None:
        if not isinstance(self.frequency_hz, ClosedFloatInterval):
            raise ValueError("frequency derating band has an invalid interval")
        _require_finite(self.gain_upper, "frequency derating gain_upper", minimum=1.0)


@dataclass(frozen=True, slots=True)
class SourceFrequencyDeratingConstraint:
    bands: tuple[SourceFrequencyDeratingBand, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.bands, tuple) or not self.bands or any(
            not isinstance(band, SourceFrequencyDeratingBand) for band in self.bands
        ):
            raise ValueError("frequency derating bands have an invalid type")
        previous: float | None = None
        for band in self.bands:
            if previous is not None and band.frequency_hz.minimum <= previous:
                raise ValueError("frequency derating bands must be increasing and disjoint")
            previous = band.frequency_hz.maximum


@dataclass(frozen=True, slots=True)
class SourceModulationEnvelopeConstraint:
    kind: SourceModulationKind
    gain_upper: float

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SourceModulationKind):
            raise ValueError("modulation envelope kind has an invalid type")
        _require_finite(self.gain_upper, "modulation envelope gain_upper", minimum=1.0)


@dataclass(frozen=True, slots=True)
class SourceArbitraryOvershootConstraint:
    gain_upper: float

    def __post_init__(self) -> None:
        _require_finite(self.gain_upper, "arbitrary overshoot gain_upper", minimum=1.0)


@dataclass(frozen=True, slots=True)
class SourceNoisePeakConstraint:
    absolute_peak_upper_v: float

    def __post_init__(self) -> None:
        _require_finite(self.absolute_peak_upper_v, "noise absolute_peak_upper_v", minimum=0.0)


@dataclass(frozen=True, slots=True)
class SourceSharedPowerConstraint:
    participants: tuple[int, ...]
    maximum_power_w: float

    def __post_init__(self) -> None:
        _require_positive_channels(self.participants, "shared power participants")
        _require_finite(self.maximum_power_w, "shared power maximum_power_w", minimum=0.0)


SourceSafetyConstraintProfile: TypeAlias = (
    SourceVoltageReferenceConstraint
    | SourceResistanceConstraint
    | SourceFrequencyDeratingConstraint
    | SourceModulationEnvelopeConstraint
    | SourceArbitraryOvershootConstraint
    | SourceNoisePeakConstraint
    | SourceSharedPowerConstraint
)


_SAFETY_PROFILE_TYPES: dict[SourceSafetyConstraintKind, type[object]] = {
    SourceSafetyConstraintKind.VOLTAGE_REFERENCE: SourceVoltageReferenceConstraint,
    SourceSafetyConstraintKind.SOURCE_RESISTANCE: SourceResistanceConstraint,
    SourceSafetyConstraintKind.FREQUENCY_DERATING: SourceFrequencyDeratingConstraint,
    SourceSafetyConstraintKind.MODULATION_ENVELOPE: SourceModulationEnvelopeConstraint,
    SourceSafetyConstraintKind.ARBITRARY_OVERSHOOT: SourceArbitraryOvershootConstraint,
    SourceSafetyConstraintKind.NOISE_PEAK: SourceNoisePeakConstraint,
    SourceSafetyConstraintKind.SHARED_POWER: SourceSharedPowerConstraint,
}


@dataclass(frozen=True, slots=True)
class SourceSafetyConstraint:
    constraint_id: str
    kind: SourceSafetyConstraintKind
    applicability: SourceConstraintApplicability
    profile: SourceSafetyConstraintProfile
    proof_strength: BudgetProofStrength
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_token(self.constraint_id, "source safety constraint_id")
        if not isinstance(self.kind, SourceSafetyConstraintKind):
            raise ValueError("source safety kind has an invalid type")
        if not isinstance(self.applicability, SourceConstraintApplicability):
            raise ValueError("source safety applicability has an invalid type")
        expected = _SAFETY_PROFILE_TYPES[self.kind]
        if not isinstance(self.profile, expected):
            raise ValueError(f"source safety kind {self.kind.value!r} requires {expected.__name__}")
        if not isinstance(self.proof_strength, BudgetProofStrength):
            raise ValueError("source safety proof_strength has an invalid type")
        _require_evidence_ref_tuple(self.evidence_refs, "source safety evidence_refs")


@dataclass(frozen=True, slots=True)
class SourceSafetyProfile:
    constraints: tuple[SourceSafetyConstraint, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.constraints, tuple) or any(
            not isinstance(item, SourceSafetyConstraint) for item in self.constraints
        ):
            raise ValueError("source safety constraints have an invalid type")
        ids = tuple(item.constraint_id for item in self.constraints)
        if len(set(ids)) != len(ids) or tuple(sorted(ids)) != ids:
            raise ValueError("source safety constraints must be sorted by constraint_id and unique")


def _require_observed(value: object, label: str) -> None:
    if not isinstance(value, Observed):
        raise ValueError(f"{label} must be Observed")


@dataclass(frozen=True, slots=True)
class SourceAmplitude:
    value: float
    unit: SourceAmplitudeUnit

    def __post_init__(self) -> None:
        _require_finite(self.value, "source amplitude value")
        if not isinstance(self.unit, SourceAmplitudeUnit):
            raise ValueError("source amplitude unit has an invalid type")


@dataclass(frozen=True, slots=True)
class SourceDisplayLoad:
    kind: SourceLoadKind
    resistance_ohm: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SourceLoadKind):
            raise ValueError("source display load kind has an invalid type")
        if self.kind is SourceLoadKind.RESISTIVE:
            _require_finite(
                self.resistance_ohm,
                "source display load resistance_ohm",
                minimum=0.0,
            )
            if self.resistance_ohm == 0:
                raise ValueError("source display load resistance_ohm must be positive")
        elif self.resistance_ohm is not None:
            raise ValueError("non-resistive source display load cannot carry resistance_ohm")


@dataclass(frozen=True, slots=True)
class SourceComponentAmplitude:
    kind: ComponentAmplitudeKind
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ComponentAmplitudeKind):
            raise ValueError("source component amplitude kind has an invalid type")
        _require_finite(self.value, "source component amplitude value")
        if self.kind is not ComponentAmplitudeKind.RELATIVE_DB and self.value < 0:
            raise ValueError("linear or Vpp component amplitudes must be non-negative")


@dataclass(frozen=True, slots=True)
class SourceHarmonicComponentV2:
    order: int
    amplitude: Observed[SourceComponentAmplitude]
    phase_deg: Observed[float]

    def __post_init__(self) -> None:
        _require_int(self.order, "source harmonic order", minimum=2)
        _require_observed(self.amplitude, "source harmonic amplitude")
        _require_observed(self.phase_deg, "source harmonic phase_deg")
        if self.phase_deg.availability is Availability.VALUE:
            _require_finite(self.phase_deg.value, "source harmonic phase_deg", minimum=0, maximum=360)


@dataclass(frozen=True, slots=True)
class SourceModulationParameter:
    kind: SourceModulationParameterKind
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SourceModulationParameterKind):
            raise ValueError("source modulation parameter kind has an invalid type")
        _require_finite(self.value, "source modulation parameter value")


@dataclass(frozen=True, slots=True)
class SourceTriggerState:
    source: Observed[SourceTriggerSource]
    slope: Observed[SourceTriggerSlope]
    output: Observed[SourceTriggerOutput]

    def __post_init__(self) -> None:
        _require_observed(self.source, "source trigger source")
        _require_observed(self.slope, "source trigger slope")
        _require_observed(self.output, "source trigger output")


@dataclass(frozen=True, slots=True)
class SourceSweepMarker:
    enabled: Observed[bool]
    frequency_hz: Observed[float]

    def __post_init__(self) -> None:
        _require_observed(self.enabled, "source sweep marker enabled")
        _require_observed(self.frequency_hz, "source sweep marker frequency_hz")
        if self.frequency_hz.availability is Availability.VALUE:
            _require_finite(
                self.frequency_hz.value,
                "source sweep marker frequency_hz",
                minimum=0.0,
            )


@dataclass(frozen=True, slots=True)
class BasicWaveFacet:
    waveform_kind: Observed[SourceWaveformKind]
    waveform_id: Observed[str]
    frequency_mode: Observed[SourceFrequencyMode]
    frequency_hz: Observed[float]
    amplitude: Observed[SourceAmplitude]
    offset_v: Observed[float]
    phase_deg: Observed[float]
    square_duty_cycle_percent: Observed[float]

    def __post_init__(self) -> None:
        for name, value in (
            ("waveform_kind", self.waveform_kind),
            ("waveform_id", self.waveform_id),
            ("frequency_mode", self.frequency_mode),
            ("frequency_hz", self.frequency_hz),
            ("amplitude", self.amplitude),
            ("offset_v", self.offset_v),
            ("phase_deg", self.phase_deg),
            ("square_duty_cycle_percent", self.square_duty_cycle_percent),
        ):
            _require_observed(value, f"basic {name}")
        if self.waveform_id.availability is Availability.VALUE:
            _require_token(self.waveform_id.value, "basic waveform_id")
        if self.frequency_hz.availability is Availability.VALUE:
            _require_finite(self.frequency_hz.value, "basic frequency_hz", minimum=0.0)
        if self.offset_v.availability is Availability.VALUE:
            _require_finite(self.offset_v.value, "basic offset_v")
        if self.phase_deg.availability is Availability.VALUE:
            _require_finite(self.phase_deg.value, "basic phase_deg", minimum=0, maximum=360)
        if self.square_duty_cycle_percent.availability is Availability.VALUE:
            _require_finite(
                self.square_duty_cycle_percent.value,
                "basic square_duty_cycle_percent",
                minimum=0,
                maximum=100,
            )


@dataclass(frozen=True, slots=True)
class OutputFacet:
    enabled: Observed[bool]
    display_load: Observed[SourceDisplayLoad]
    polarity: Observed[SourceOutputPolarity]

    def __post_init__(self) -> None:
        _require_observed(self.enabled, "output enabled")
        _require_observed(self.display_load, "output display_load")
        _require_observed(self.polarity, "output polarity")
        if self.enabled.availability is Availability.VALUE:
            _require_bool(self.enabled.value, "output enabled value")


@dataclass(frozen=True, slots=True)
class SourceBasicPatch:
    waveform_kind: PatchValue[SourceWaveformKind] = PatchValue(PatchAction.KEEP)
    frequency_hz: PatchValue[float] = PatchValue(PatchAction.KEEP)
    amplitude_vpp: PatchValue[float] = PatchValue(PatchAction.KEEP)
    offset_v: PatchValue[float] = PatchValue(PatchAction.KEEP)
    square_duty_cycle_percent: PatchValue[float] = PatchValue(PatchAction.KEEP)

    def __post_init__(self) -> None:
        values = (
            ("waveform_kind", self.waveform_kind),
            ("frequency_hz", self.frequency_hz),
            ("amplitude_vpp", self.amplitude_vpp),
            ("offset_v", self.offset_v),
            ("square_duty_cycle_percent", self.square_duty_cycle_percent),
        )
        if any(not isinstance(value, PatchValue) for _, value in values):
            raise ValueError("source basic patch values must be PatchValue")
        if not any(value.action is PatchAction.SET for _, value in values):
            raise ValueError("source basic patch requires at least one SET value")
        if self.waveform_kind.action is PatchAction.SET and not isinstance(
            self.waveform_kind.value,
            SourceWaveformKind,
        ):
            raise ValueError("source basic patch waveform_kind must be SourceWaveformKind")
        if self.waveform_kind.action is PatchAction.SET and self.waveform_kind.value in {
            SourceWaveformKind.ARBITRARY,
            SourceWaveformKind.OTHER,
        }:
            raise ValueError(
                "source basic patch waveform_kind cannot configure arbitrary or other waveforms"
            )
        for label, value, minimum, maximum in (
            ("frequency_hz", self.frequency_hz, 0.0, None),
            ("amplitude_vpp", self.amplitude_vpp, 0.0, None),
            ("offset_v", self.offset_v, None, None),
            ("square_duty_cycle_percent", self.square_duty_cycle_percent, 0.0, 100.0),
        ):
            if value.action is PatchAction.SET:
                _require_finite(
                    value.value,
                    f"source basic patch {label}",
                    minimum=minimum,
                    maximum=maximum,
                )


@dataclass(frozen=True, slots=True)
class SourceBasicConfigureRequest:
    channel: int
    patch: SourceBasicPatch
    mode: PatchMode = PatchMode.PATCH

    def __post_init__(self) -> None:
        _require_int(self.channel, "source basic configure channel", minimum=1)
        if not isinstance(self.patch, SourceBasicPatch):
            raise ValueError("source basic configure patch has an invalid type")
        if not isinstance(self.mode, PatchMode):
            raise ValueError("source basic configure mode has an invalid type")
        if self.mode is not PatchMode.PATCH:
            raise ValueError("source basic configure only supports PATCH mode")


@dataclass(frozen=True, slots=True)
class SourceOutputRequest:
    channel: int
    enabled: bool

    def __post_init__(self) -> None:
        _require_int(self.channel, "source output channel", minimum=1)
        _require_bool(self.enabled, "source output enabled")


@dataclass(frozen=True, slots=True)
class SourceHarmonicConfigureRequest:
    channel: int
    order: int
    preset: SourceHarmonicPreset

    def __post_init__(self) -> None:
        _require_int(self.channel, "source harmonic configure channel", minimum=1)
        _require_int(self.order, "source harmonic configure order", minimum=2)
        if not isinstance(self.preset, SourceHarmonicPreset):
            raise ValueError("source harmonic configure preset has an invalid type")


@dataclass(frozen=True, slots=True)
class SourceBasicConfigureResult:
    channel: int
    basic: BasicWaveFacet
    output_enabled: bool

    def __post_init__(self) -> None:
        _require_int(self.channel, "source basic configure result channel", minimum=1)
        if not isinstance(self.basic, BasicWaveFacet):
            raise ValueError("source basic configure result basic has an invalid type")
        _require_bool(self.output_enabled, "source basic configure result output_enabled")
        if self.output_enabled:
            raise ValueError("source basic configure result requires output_enabled=False")
        if (
            self.basic.amplitude.availability is not Availability.VALUE
            or not isinstance(self.basic.amplitude.value, SourceAmplitude)
            or self.basic.amplitude.value.unit is not SourceAmplitudeUnit.VPP
        ):
            raise ValueError(
                "source basic configure result requires a final VPP amplitude readback"
            )
        _require_finite(
            self.basic.amplitude.value.value,
            "source basic configure result final_amplitude",
            minimum=0.0,
        )
        if self.basic.offset_v.availability is not Availability.VALUE:
            raise ValueError(
                "source basic configure result requires a final offset readback"
            )
        _require_finite(
            self.basic.offset_v.value,
            "source basic configure result final_offset_v",
        )


@dataclass(frozen=True, slots=True)
class SourceOutputResult:
    channel: int
    enabled: bool
    final_amplitude: SourceAmplitude | None = None
    final_offset_v: float | None = None

    def __post_init__(self) -> None:
        _require_int(self.channel, "source output result channel", minimum=1)
        _require_bool(self.enabled, "source output result enabled")
        if self.final_amplitude is not None:
            if not isinstance(self.final_amplitude, SourceAmplitude):
                raise ValueError("source output result final_amplitude has an invalid type")
            if self.final_amplitude.unit is not SourceAmplitudeUnit.VPP:
                raise ValueError("source output result final_amplitude must use VPP")
            _require_finite(
                self.final_amplitude.value,
                "source output result final_amplitude value",
                minimum=0.0,
            )
        if self.final_offset_v is not None:
            _require_finite(
                self.final_offset_v,
                "source output result final_offset_v",
            )
        if self.enabled and (self.final_amplitude is None or self.final_offset_v is None):
            raise ValueError(
                "enabled source output results require final_amplitude and final_offset_v"
            )


@dataclass(frozen=True, slots=True)
class HarmonicFacet:
    enabled: Observed[bool]
    completeness: Observed[HarmonicCompleteness]
    maximum_supported_order: Observed[int]
    components: Observed[tuple[SourceHarmonicComponentV2, ...]]
    configured_order: Observed[int] = field(
        default_factory=lambda: Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        )
    )
    preset: Observed[SourceHarmonicPreset] = field(
        default_factory=lambda: Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        )
    )

    def __post_init__(self) -> None:
        _require_observed(self.enabled, "harmonic enabled")
        _require_observed(self.completeness, "harmonic completeness")
        _require_observed(self.maximum_supported_order, "harmonic maximum_supported_order")
        _require_observed(self.components, "harmonic components")
        _require_observed(self.configured_order, "harmonic configured_order")
        _require_observed(self.preset, "harmonic preset")
        if self.enabled.availability is Availability.VALUE:
            _require_bool(self.enabled.value, "harmonic enabled value")
        if self.maximum_supported_order.availability is Availability.VALUE:
            _require_int(
                self.maximum_supported_order.value,
                "harmonic maximum_supported_order value",
                minimum=2,
            )
        if self.configured_order.availability is Availability.VALUE:
            _require_int(
                self.configured_order.value,
                "harmonic configured_order value",
                minimum=2,
            )
        if self.preset.availability is Availability.VALUE and not isinstance(
            self.preset.value,
            SourceHarmonicPreset,
        ):
            raise ValueError("harmonic preset value has an invalid type")
        if self.components.availability is Availability.VALUE:
            components = self.components.value
            if not isinstance(components, tuple) or any(
                not isinstance(item, SourceHarmonicComponentV2) for item in components
            ):
                raise ValueError("harmonic components value has an invalid type")
            orders = tuple(item.order for item in components)
            if len(set(orders)) != len(orders) or tuple(sorted(orders)) != orders:
                raise ValueError("harmonic components must be sorted by order and unique")


@dataclass(frozen=True, slots=True)
class SourceHarmonicConfigureResult:
    channel: int
    harmonics: HarmonicFacet
    output_enabled: bool

    def __post_init__(self) -> None:
        _require_int(self.channel, "source harmonic configure result channel", minimum=1)
        if not isinstance(self.harmonics, HarmonicFacet):
            raise ValueError("source harmonic configure result harmonics has an invalid type")
        _require_bool(self.output_enabled, "source harmonic configure result output_enabled")
        if self.output_enabled:
            raise ValueError("source harmonic configure result requires output_enabled=False")
        if (
            self.harmonics.enabled.availability is not Availability.VALUE
            or self.harmonics.enabled.value is not True
        ):
            raise ValueError("source harmonic configure result requires enabled harmonic readback")
        if self.harmonics.configured_order.availability is not Availability.VALUE:
            raise ValueError("source harmonic configure result requires configured_order readback")
        if self.harmonics.preset.availability is not Availability.VALUE:
            raise ValueError("source harmonic configure result requires preset readback")


@dataclass(frozen=True, slots=True)
class ModulationFacet:
    enabled: Observed[bool]
    kind: Observed[SourceModulationKind]
    source: Observed[SourceModulationSource]
    parameters: Observed[tuple[SourceModulationParameter, ...]]
    internal_frequency_hz: Observed[float]
    internal_waveform_kind: Observed[SourceWaveformKind]

    def __post_init__(self) -> None:
        for name, value in (
            ("enabled", self.enabled),
            ("kind", self.kind),
            ("source", self.source),
            ("parameters", self.parameters),
            ("internal_frequency_hz", self.internal_frequency_hz),
            ("internal_waveform_kind", self.internal_waveform_kind),
        ):
            _require_observed(value, f"modulation {name}")
        if self.enabled.availability is Availability.VALUE:
            _require_bool(self.enabled.value, "modulation enabled value")
        if self.parameters.availability is Availability.VALUE:
            parameters = self.parameters.value
            if not isinstance(parameters, tuple) or any(
                not isinstance(item, SourceModulationParameter) for item in parameters
            ):
                raise ValueError("modulation parameters value has an invalid type")
            kinds = tuple(item.kind.value for item in parameters)
            if len(set(kinds)) != len(kinds) or tuple(sorted(kinds)) != kinds:
                raise ValueError("modulation parameters must be sorted by kind and unique")
        if self.internal_frequency_hz.availability is Availability.VALUE:
            _require_finite(
                self.internal_frequency_hz.value,
                "modulation internal_frequency_hz",
                minimum=0.0,
            )


@dataclass(frozen=True, slots=True)
class SweepFacet:
    enabled: Observed[bool]
    start_hz: Observed[float]
    stop_hz: Observed[float]
    spacing: Observed[SourceSweepSpacing]
    steps: Observed[int]
    sweep_time_s: Observed[float]
    start_hold_s: Observed[float]
    stop_hold_s: Observed[float]
    return_time_s: Observed[float]
    trigger: Observed[SourceTriggerState]
    marker: Observed[SourceSweepMarker]

    def __post_init__(self) -> None:
        for name, value in (
            ("enabled", self.enabled),
            ("start_hz", self.start_hz),
            ("stop_hz", self.stop_hz),
            ("spacing", self.spacing),
            ("steps", self.steps),
            ("sweep_time_s", self.sweep_time_s),
            ("start_hold_s", self.start_hold_s),
            ("stop_hold_s", self.stop_hold_s),
            ("return_time_s", self.return_time_s),
            ("trigger", self.trigger),
            ("marker", self.marker),
        ):
            _require_observed(value, f"sweep {name}")
        if self.enabled.availability is Availability.VALUE:
            _require_bool(self.enabled.value, "sweep enabled value")
        for name, value in (
            ("start_hz", self.start_hz),
            ("stop_hz", self.stop_hz),
            ("sweep_time_s", self.sweep_time_s),
            ("start_hold_s", self.start_hold_s),
            ("stop_hold_s", self.stop_hold_s),
            ("return_time_s", self.return_time_s),
        ):
            if value.availability is Availability.VALUE:
                _require_finite(value.value, f"sweep {name} value", minimum=0.0)
        if self.steps.availability is Availability.VALUE:
            _require_int(self.steps.value, "sweep steps value", minimum=2)


@dataclass(frozen=True, slots=True)
class BurstFacet:
    enabled: Observed[bool]
    mode: Observed[SourceBurstMode]
    cycles: Observed[int]
    phase_deg: Observed[float]
    internal_period_s: Observed[float]
    delay_s: Observed[float]
    gate_polarity: Observed[SourceGatePolarity]
    trigger: Observed[SourceTriggerState]

    def __post_init__(self) -> None:
        for name, value in (
            ("enabled", self.enabled),
            ("mode", self.mode),
            ("cycles", self.cycles),
            ("phase_deg", self.phase_deg),
            ("internal_period_s", self.internal_period_s),
            ("delay_s", self.delay_s),
            ("gate_polarity", self.gate_polarity),
            ("trigger", self.trigger),
        ):
            _require_observed(value, f"burst {name}")
        if self.enabled.availability is Availability.VALUE:
            _require_bool(self.enabled.value, "burst enabled value")
        if self.cycles.availability is Availability.VALUE:
            _require_int(self.cycles.value, "burst cycles value", minimum=1)
        if self.phase_deg.availability is Availability.VALUE:
            _require_finite(self.phase_deg.value, "burst phase_deg value", minimum=0, maximum=360)
        for name, value in (
            ("internal_period_s", self.internal_period_s),
            ("delay_s", self.delay_s),
        ):
            if value.availability is Availability.VALUE:
                _require_finite(value.value, f"burst {name} value", minimum=0.0)


@dataclass(frozen=True, slots=True)
class PulseFacet:
    hold_basis: Observed[SourcePulseHoldBasis]
    width_s: Observed[float]
    duty_cycle_percent: Observed[float]
    delay_s: Observed[float]
    leading_transition_s: Observed[float]
    trailing_transition_s: Observed[float]

    def __post_init__(self) -> None:
        for name, value in (
            ("hold_basis", self.hold_basis),
            ("width_s", self.width_s),
            ("duty_cycle_percent", self.duty_cycle_percent),
            ("delay_s", self.delay_s),
            ("leading_transition_s", self.leading_transition_s),
            ("trailing_transition_s", self.trailing_transition_s),
        ):
            _require_observed(value, f"pulse {name}")
        for name, value in (
            ("width_s", self.width_s),
            ("delay_s", self.delay_s),
            ("leading_transition_s", self.leading_transition_s),
            ("trailing_transition_s", self.trailing_transition_s),
        ):
            if value.availability is Availability.VALUE:
                _require_finite(value.value, f"pulse {name} value", minimum=0.0)
        if self.duty_cycle_percent.availability is Availability.VALUE:
            _require_finite(
                self.duty_cycle_percent.value,
                "pulse duty_cycle_percent value",
                minimum=0,
                maximum=100,
            )


@dataclass(frozen=True, slots=True)
class ArbitraryFacet:
    selected_waveform_id: Observed[str]
    playback_mode: Observed[SourceArbitraryPlaybackMode]
    playback_frequency_hz: Observed[float]
    sample_rate_hz: Observed[float]
    point_count: Observed[int]
    storage_digest: Observed[str]

    def __post_init__(self) -> None:
        for name, value in (
            ("selected_waveform_id", self.selected_waveform_id),
            ("playback_mode", self.playback_mode),
            ("playback_frequency_hz", self.playback_frequency_hz),
            ("sample_rate_hz", self.sample_rate_hz),
            ("point_count", self.point_count),
            ("storage_digest", self.storage_digest),
        ):
            _require_observed(value, f"arbitrary {name}")
        if self.selected_waveform_id.availability is Availability.VALUE:
            _require_token(self.selected_waveform_id.value, "arbitrary selected_waveform_id")
        for name, value in (
            ("playback_frequency_hz", self.playback_frequency_hz),
            ("sample_rate_hz", self.sample_rate_hz),
        ):
            if value.availability is Availability.VALUE:
                _require_finite(value.value, f"arbitrary {name} value", minimum=0.0)
        if self.point_count.availability is Availability.VALUE:
            _require_int(self.point_count.value, "arbitrary point_count value", minimum=1)
        if self.storage_digest.availability is Availability.VALUE and (
            not isinstance(self.storage_digest.value, str)
            or _SHA256.fullmatch(self.storage_digest.value) is None
        ):
            raise ValueError("arbitrary storage_digest must be sha256:<64 lowercase hex>")


@dataclass(frozen=True, slots=True)
class SourceCounterMeasurementV2:
    kind: SourceCounterMeasurementKind
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SourceCounterMeasurementKind):
            raise ValueError("counter measurement kind has an invalid type")
        _require_finite(self.value, "counter measurement value")


@dataclass(frozen=True, slots=True)
class SourceCounterInputState:
    input_id: str
    enabled: Observed[bool]
    measurements: Observed[tuple[SourceCounterMeasurementV2, ...]]
    coupling: Observed[SourceInputCoupling]
    impedance_ohm: Observed[float]
    attenuation: Observed[int]
    gate_time_s: Observed[float]
    trigger_level_v: Observed[float]
    statistics_enabled: Observed[bool]

    def __post_init__(self) -> None:
        _require_token(self.input_id, "counter input_id")
        for name, value in (
            ("enabled", self.enabled),
            ("measurements", self.measurements),
            ("coupling", self.coupling),
            ("impedance_ohm", self.impedance_ohm),
            ("attenuation", self.attenuation),
            ("gate_time_s", self.gate_time_s),
            ("trigger_level_v", self.trigger_level_v),
            ("statistics_enabled", self.statistics_enabled),
        ):
            _require_observed(value, f"counter {name}")
        if self.enabled.availability is Availability.VALUE:
            _require_bool(self.enabled.value, "counter enabled value")
        if self.statistics_enabled.availability is Availability.VALUE:
            _require_bool(self.statistics_enabled.value, "counter statistics_enabled value")
        if self.measurements.availability is Availability.VALUE:
            measurements = self.measurements.value
            if not isinstance(measurements, tuple) or any(
                not isinstance(item, SourceCounterMeasurementV2) for item in measurements
            ):
                raise ValueError("counter measurements value has an invalid type")
            kinds = tuple(item.kind.value for item in measurements)
            if len(set(kinds)) != len(kinds) or tuple(sorted(kinds)) != kinds:
                raise ValueError("counter measurements must be sorted by kind and unique")
        if self.impedance_ohm.availability is Availability.VALUE:
            _require_finite(self.impedance_ohm.value, "counter impedance_ohm", minimum=0.0)
        if self.attenuation.availability is Availability.VALUE:
            _require_int(self.attenuation.value, "counter attenuation", minimum=1)
        if self.gate_time_s.availability is Availability.VALUE:
            _require_finite(self.gate_time_s.value, "counter gate_time_s", minimum=0.0)
        if self.trigger_level_v.availability is Availability.VALUE:
            _require_finite(self.trigger_level_v.value, "counter trigger_level_v")


@dataclass(frozen=True, slots=True)
class SourceReferenceClockState:
    mode: Observed[SourceReferenceClockMode]
    frequency_hz: Observed[float]
    locked: Observed[bool]

    def __post_init__(self) -> None:
        _require_observed(self.mode, "reference clock mode")
        _require_observed(self.frequency_hz, "reference clock frequency_hz")
        _require_observed(self.locked, "reference clock locked")
        if self.frequency_hz.availability is Availability.VALUE:
            _require_finite(self.frequency_hz.value, "reference clock frequency_hz", minimum=0.0)
        if self.locked.availability is Availability.VALUE:
            _require_bool(self.locked.value, "reference clock locked value")


@dataclass(frozen=True, slots=True)
class SourceSyncState:
    enabled: Observed[bool]
    polarity: Observed[SourceOutputPolarity]
    source_channel: Observed[int]

    def __post_init__(self) -> None:
        _require_observed(self.enabled, "sync enabled")
        _require_observed(self.polarity, "sync polarity")
        _require_observed(self.source_channel, "sync source_channel")
        if self.enabled.availability is Availability.VALUE:
            _require_bool(self.enabled.value, "sync enabled value")
        if self.source_channel.availability is Availability.VALUE:
            _require_int(self.source_channel.value, "sync source_channel value", minimum=1)


@dataclass(frozen=True, slots=True)
class SourceCascadeState:
    enabled: Observed[bool]
    role: Observed[str]

    def __post_init__(self) -> None:
        _require_observed(self.enabled, "cascade enabled")
        _require_observed(self.role, "cascade role")
        if self.enabled.availability is Availability.VALUE:
            _require_bool(self.enabled.value, "cascade enabled value")
        if self.role.availability is Availability.VALUE:
            _require_token(self.role.value, "cascade role value")


@dataclass(frozen=True, slots=True)
class SourceRelationState:
    feature: SourceFeature
    channels: tuple[int, ...]
    enabled: Observed[bool]

    def __post_init__(self) -> None:
        if self.feature not in {
            SourceFeature.COMBINE,
            SourceFeature.TRACKING,
            SourceFeature.COUPLING,
            SourceFeature.COPY,
            SourceFeature.PHASE_RELATION,
        }:
            raise ValueError("source relation state feature is not a relation")
        _require_positive_channels(self.channels, "source relation state channels")
        if len(self.channels) < 2:
            raise ValueError("source relation state requires two or more channels")
        _require_observed(self.enabled, "source relation state enabled")
        if self.enabled.availability is Availability.VALUE:
            _require_bool(self.enabled.value, "source relation state enabled value")


@dataclass(frozen=True, slots=True)
class SourceSharedPowerState:
    participants: tuple[int, ...]
    active_power_upper_w: Observed[float]
    hard_limit_w: Observed[float]

    def __post_init__(self) -> None:
        _require_positive_channels(self.participants, "shared power state participants")
        _require_observed(self.active_power_upper_w, "shared power active_power_upper_w")
        _require_observed(self.hard_limit_w, "shared power hard_limit_w")
        for name, value in (
            ("active_power_upper_w", self.active_power_upper_w),
            ("hard_limit_w", self.hard_limit_w),
        ):
            if value.availability is Availability.VALUE:
                _require_finite(value.value, f"shared power {name} value", minimum=0.0)


@dataclass(frozen=True, slots=True)
class SourceSystemStateV2:
    counters: tuple[SourceCounterInputState, ...]
    reference_clock: Observed[SourceReferenceClockState]
    sync: Observed[SourceSyncState]
    cascade: Observed[SourceCascadeState]

    def __post_init__(self) -> None:
        if not isinstance(self.counters, tuple) or any(
            not isinstance(item, SourceCounterInputState) for item in self.counters
        ):
            raise ValueError("source system counters have an invalid type")
        ids = tuple(item.input_id for item in self.counters)
        if len(set(ids)) != len(ids) or tuple(sorted(ids)) != ids:
            raise ValueError("source system counters must be sorted by input_id and unique")
        _require_observed(self.reference_clock, "source system reference_clock")
        _require_observed(self.sync, "source system sync")
        _require_observed(self.cascade, "source system cascade")


@dataclass(frozen=True, slots=True)
class SourceCrossChannelStateV2:
    relations: tuple[SourceRelationState, ...]
    relation_graph: Observed[SourceRelationGraph]
    shared_power: Observed[SourceSharedPowerState]

    def __post_init__(self) -> None:
        if not isinstance(self.relations, tuple) or any(
            not isinstance(item, SourceRelationState) for item in self.relations
        ):
            raise ValueError("source cross-channel relations have an invalid type")
        keys = tuple((item.feature.value, item.channels) for item in self.relations)
        if len(set(keys)) != len(keys) or tuple(sorted(keys)) != keys:
            raise ValueError("source cross-channel relations must be sorted and unique")
        _require_observed(self.relation_graph, "source cross-channel relation_graph")
        _require_observed(self.shared_power, "source cross-channel shared_power")


@dataclass(frozen=True, slots=True)
class SourceChannelStateV2:
    channel: int
    basic: Observed[BasicWaveFacet]
    output: Observed[OutputFacet]
    harmonics: Observed[HarmonicFacet]
    modulation: Observed[ModulationFacet]
    sweep: Observed[SweepFacet]
    burst: Observed[BurstFacet]
    pulse: Observed[PulseFacet]
    arbitrary: Observed[ArbitraryFacet]

    def __post_init__(self) -> None:
        _require_int(self.channel, "source channel state channel", minimum=1)
        for name, value in (
            ("basic", self.basic),
            ("output", self.output),
            ("harmonics", self.harmonics),
            ("modulation", self.modulation),
            ("sweep", self.sweep),
            ("burst", self.burst),
            ("pulse", self.pulse),
            ("arbitrary", self.arbitrary),
        ):
            _require_observed(value, f"source channel state {name}")


@dataclass(frozen=True, slots=True)
class SourceRuntimeCapabilityProfile:
    session_epoch: str
    descriptor_digest: str
    identity: SourceRuntimeIdentity
    features: tuple[SourceFeatureCapability, ...]

    def __post_init__(self) -> None:
        _require_token(self.session_epoch, "source runtime session_epoch")
        if not isinstance(self.descriptor_digest, str) or _SHA256.fullmatch(
            self.descriptor_digest
        ) is None:
            raise ValueError("source runtime descriptor_digest must be sha256:<64 lowercase hex>")
        if not isinstance(self.identity, SourceRuntimeIdentity):
            raise ValueError("source runtime identity has an invalid type")
        if not isinstance(self.features, tuple) or any(
            not isinstance(item, SourceFeatureCapability) for item in self.features
        ):
            raise ValueError("source runtime features have an invalid type")
        keys = tuple(
            (item.feature.value, item.scope.value, item.channels) for item in self.features
        )
        if len(set(keys)) != len(keys) or tuple(sorted(keys)) != keys:
            raise ValueError("source runtime features must be sorted and unique")


class SourceQueryPhase(StrEnum):
    ANCHOR_BEFORE = "anchor_before"
    FACET = "facet"
    ANCHOR_AFTER = "anchor_after"


@dataclass(frozen=True, slots=True)
class SourceFacetQueryContract:
    feature: SourceFeature
    scope: SourceFacetScope
    fields: tuple[SourceFieldId, ...]
    activation_any: tuple[SourceActivationRule, ...]
    effect: SourceQueryEffect
    max_queries: int
    required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.feature, SourceFeature):
            raise ValueError("source query feature has an invalid type")
        if not isinstance(self.scope, SourceFacetScope):
            raise ValueError("source query scope has an invalid type")
        _require_enum_tuple(self.fields, SourceFieldId, "source query fields")
        if any(self.scope not in _FIELD_SCOPES[field] for field in self.fields):
            raise ValueError("source query field does not support the declared scope")
        if not isinstance(self.activation_any, tuple) or any(
            not isinstance(item, SourceActivationRule) for item in self.activation_any
        ):
            raise ValueError("source query activation_any has an invalid type")
        if not isinstance(self.effect, SourceQueryEffect):
            raise ValueError("source query effect has an invalid type")
        _require_int(self.max_queries, "source query max_queries", minimum=1)
        _require_bool(self.required, "source query required")


@dataclass(frozen=True, slots=True)
class SourceQueryContract:
    anchor_fields: tuple[SourceFieldId, ...]
    facets: tuple[SourceFacetQueryContract, ...]
    max_queries: int
    timeout_ms: int

    def __post_init__(self) -> None:
        _require_enum_tuple(self.anchor_fields, SourceFieldId, "source query anchor_fields")
        if SourceFieldId.IDENTITY not in self.anchor_fields:
            raise ValueError("source query anchor_fields must include source.identity")
        if not isinstance(self.facets, tuple) or not self.facets or any(
            not isinstance(item, SourceFacetQueryContract) for item in self.facets
        ):
            raise ValueError("source query facets have an invalid type")
        keys = tuple(
            (
                item.feature.value,
                item.scope.value,
                tuple(field.value for field in item.fields),
            )
            for item in self.facets
        )
        if len(set(keys)) != len(keys) or tuple(sorted(keys)) != keys:
            raise ValueError("source query facets must be sorted and unique")
        covered = {field for item in self.facets for field in item.fields}
        if not set(self.anchor_fields) <= covered:
            raise ValueError("source query facets do not cover every anchor field")
        _require_int(self.max_queries, "source query max_queries", minimum=1)
        _require_int(self.timeout_ms, "source query timeout_ms", minimum=1)


@dataclass(frozen=True, slots=True)
class SourceSemanticQueryItem:
    item_id: str
    phase: SourceQueryPhase
    feature: SourceFeature
    target: SourceScopeRef
    fields: tuple[SourceFieldRef, ...]
    activation_any: tuple[SourceActivationRule, ...]
    required: bool
    effect: SourceQueryEffect
    max_queries: int

    def __post_init__(self) -> None:
        _require_token(self.item_id, "source semantic query item_id")
        if not isinstance(self.phase, SourceQueryPhase):
            raise ValueError("source semantic query phase has an invalid type")
        if not isinstance(self.feature, SourceFeature):
            raise ValueError("source semantic query feature has an invalid type")
        if not isinstance(self.target, SourceScopeRef):
            raise ValueError("source semantic query target has an invalid type")
        if not isinstance(self.fields, tuple) or not self.fields or any(
            not isinstance(item, SourceFieldRef) for item in self.fields
        ):
            raise ValueError("source semantic query fields have an invalid type")
        if any(item.target != self.target for item in self.fields):
            raise ValueError("source semantic query fields must use the item target")
        field_values = tuple(item.field.value for item in self.fields)
        if len(set(field_values)) != len(field_values) or tuple(sorted(field_values)) != field_values:
            raise ValueError("source semantic query fields must be sorted and unique")
        if not isinstance(self.activation_any, tuple) or any(
            not isinstance(item, SourceActivationRule) for item in self.activation_any
        ):
            raise ValueError("source semantic query activation_any has an invalid type")
        _require_bool(self.required, "source semantic query required")
        if not isinstance(self.effect, SourceQueryEffect):
            raise ValueError("source semantic query effect has an invalid type")
        _require_int(self.max_queries, "source semantic query max_queries", minimum=1)


@dataclass(frozen=True, slots=True)
class SourceSemanticQueryPlan:
    contract_version: Literal["wavebench.source.v2"]
    plan_id: str
    items: tuple[SourceSemanticQueryItem, ...]
    allowed_effects: tuple[SourceQueryEffect, ...]
    max_queries: int
    deadline_monotonic: float

    def __post_init__(self) -> None:
        if self.contract_version != SOURCE_CONTRACT_VERSION:
            raise ValueError("source semantic query contract_version is unsupported")
        _require_token(self.plan_id, "source semantic query plan_id")
        if not isinstance(self.items, tuple) or any(
            not isinstance(item, SourceSemanticQueryItem) for item in self.items
        ):
            raise ValueError("source semantic query items have an invalid type")
        item_ids = tuple(item.item_id for item in self.items)
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("source semantic query item_ids must be unique")
        phases = tuple(item.phase.value for item in self.items)
        if phases != tuple(sorted(phases, key=("anchor_before", "facet", "anchor_after").index)):
            raise ValueError("source semantic query items have an invalid phase order")
        _require_enum_tuple(
            self.allowed_effects,
            SourceQueryEffect,
            "source semantic query allowed_effects",
        )
        if any(item.effect not in self.allowed_effects for item in self.items):
            raise ValueError("source semantic query item effect is not allowed")
        _require_int(self.max_queries, "source semantic query max_queries", minimum=1)
        _require_finite(self.deadline_monotonic, "source semantic query deadline_monotonic", minimum=0)


class SourceQueryItemOutcome(StrEnum):
    OBSERVED = "observed"
    SEMANTIC_UNAVAILABLE = "semantic_unavailable"
    SKIPPED = "skipped"


SourceObservationValue: TypeAlias = (
    SourceRuntimeIdentity
    | BasicWaveFacet
    | OutputFacet
    | SourceDisplayLoad
    | HarmonicFacet
    | ModulationFacet
    | SweepFacet
    | BurstFacet
    | PulseFacet
    | ArbitraryFacet
    | SourceCounterInputState
    | SourceReferenceClockState
    | SourceSyncState
    | SourceCascadeState
    | SourceRelationState
    | SourceRelationGraph
    | SourceSharedPowerState
    | bool
    | str
)


_OBSERVATION_TYPES: dict[SourceFieldId, type[object] | tuple[type[object], ...]] = {
    SourceFieldId.IDENTITY: SourceRuntimeIdentity,
    SourceFieldId.BASIC: BasicWaveFacet,
    SourceFieldId.OUTPUT: OutputFacet,
    SourceFieldId.DISPLAY_LOAD: SourceDisplayLoad,
    SourceFieldId.HARMONICS: HarmonicFacet,
    SourceFieldId.MODULATION: ModulationFacet,
    SourceFieldId.SWEEP: SweepFacet,
    SourceFieldId.BURST: BurstFacet,
    SourceFieldId.PULSE: PulseFacet,
    SourceFieldId.ARBITRARY_SELECTION: ArbitraryFacet,
    SourceFieldId.ARBITRARY_STORAGE: str,
    SourceFieldId.ARM_STATE: bool,
    SourceFieldId.TRIGGER_STATE: bool,
    SourceFieldId.COMBINE: SourceRelationState,
    SourceFieldId.COUPLING: SourceRelationState,
    SourceFieldId.TRACKING: SourceRelationState,
    SourceFieldId.COPY: SourceRelationState,
    SourceFieldId.PHASE_RELATION: SourceRelationState,
    SourceFieldId.RELATION_GRAPH: SourceRelationGraph,
    SourceFieldId.REFERENCE_CLOCK: SourceReferenceClockState,
    SourceFieldId.SYNC: SourceSyncState,
    SourceFieldId.CASCADE: SourceCascadeState,
    SourceFieldId.SHARED_POWER: SourceSharedPowerState,
    SourceFieldId.COUNTER: SourceCounterInputState,
}


@dataclass(frozen=True, slots=True)
class SourceTypedObservation:
    field: SourceFieldRef
    value: SourceObservationValue
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.field, SourceFieldRef):
            raise ValueError("source typed observation field has an invalid type")
        expected = _OBSERVATION_TYPES[self.field.field]
        if expected is bool:
            if not isinstance(self.value, bool):
                raise ValueError("source typed observation value has an invalid type")
        elif not isinstance(self.value, expected):
            raise ValueError("source typed observation value has an invalid type")
        if _contains_nonfinite(self.value):
            raise ValueError("source typed observation cannot contain non-finite floats")
        _require_evidence_ref_tuple(self.evidence_refs, "source typed observation evidence_refs")


@dataclass(frozen=True, slots=True)
class SourceProtocolQueryRecord:
    item_id: str
    effect: SourceQueryEffect
    outcome: SourceQueryItemOutcome
    query_count: int
    observations: tuple[SourceTypedObservation, ...] = ()
    reason_code: SourceReasonCode | None = None

    def __post_init__(self) -> None:
        _require_token(self.item_id, "source protocol query item_id")
        if not isinstance(self.effect, SourceQueryEffect):
            raise ValueError("source protocol query effect has an invalid type")
        if not isinstance(self.outcome, SourceQueryItemOutcome):
            raise ValueError("source protocol query outcome has an invalid type")
        _require_int(self.query_count, "source protocol query_count", minimum=0)
        if not isinstance(self.observations, tuple) or any(
            not isinstance(item, SourceTypedObservation) for item in self.observations
        ):
            raise ValueError("source protocol query observations have an invalid type")
        fields_seen = tuple(
            (item.field.field.value, source_v2_canonical_json(item.field.target))
            for item in self.observations
        )
        if len(set(fields_seen)) != len(fields_seen):
            raise ValueError("source protocol query observations must be unique")
        if self.outcome is SourceQueryItemOutcome.OBSERVED:
            if not self.observations or self.reason_code is not None:
                raise ValueError("OBSERVED query records require observations and no reason_code")
        else:
            if self.observations:
                raise ValueError("non-OBSERVED query records cannot carry observations")
            if not isinstance(self.reason_code, SourceReasonCode):
                raise ValueError("non-OBSERVED query records require a registered reason_code")


@dataclass(frozen=True, slots=True)
class SourceQueryExecutionRecord:
    contract_version: Literal["wavebench.source.v2"]
    plan_id: str
    items: tuple[SourceProtocolQueryRecord, ...]
    query_count: int
    device_revision_token_before: str | None = None
    device_revision_token_after: str | None = None

    def __post_init__(self) -> None:
        if self.contract_version != SOURCE_CONTRACT_VERSION:
            raise ValueError("source query execution contract_version is unsupported")
        _require_token(self.plan_id, "source query execution plan_id")
        if not isinstance(self.items, tuple) or any(
            not isinstance(item, SourceProtocolQueryRecord) for item in self.items
        ):
            raise ValueError("source query execution items have an invalid type")
        item_ids = tuple(item.item_id for item in self.items)
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("source query execution item_ids must be unique")
        _require_int(self.query_count, "source query execution query_count", minimum=1)
        if self.query_count != sum(item.query_count for item in self.items):
            raise ValueError("source query execution query_count does not match item counts")
        for label, value in (
            ("device_revision_token_before", self.device_revision_token_before),
            ("device_revision_token_after", self.device_revision_token_after),
        ):
            if value is not None:
                _require_token(value, f"source query execution {label}")


@dataclass(frozen=True, slots=True)
class SourceDescriptorExtensions:
    contract_version: Literal["wavebench.source.v2"]
    topology: SourceTopologyContract
    features: tuple[SourceFeatureCapability, ...]
    query_contract: SourceQueryContract
    safety_profile: SourceSafetyProfile = SourceSafetyProfile()

    def __post_init__(self) -> None:
        if self.contract_version != SOURCE_CONTRACT_VERSION:
            raise ValueError("source descriptor contract_version is unsupported")
        if not isinstance(self.topology, SourceTopologyContract):
            raise ValueError("source descriptor topology has an invalid type")
        if not isinstance(self.features, tuple) or not self.features or any(
            not isinstance(item, SourceFeatureCapability) for item in self.features
        ):
            raise ValueError("source descriptor features have an invalid type")
        keys = tuple(
            (item.feature.value, item.scope.value, item.channels) for item in self.features
        )
        if len(set(keys)) != len(keys) or tuple(sorted(keys)) != keys:
            raise ValueError("source descriptor features must be sorted and unique")
        if any(not set(item.channels) <= set(self.topology.channels) for item in self.features):
            raise ValueError("source descriptor feature references an unknown channel")
        for feature in self.features:
            if isinstance(feature.profile, SourceCounterCapabilityProfile) and not set(
                feature.profile.input_ids
            ) <= set(self.topology.input_ids):
                raise ValueError("source counter profile references an unknown input_id")
            if isinstance(feature.profile, SourceCrossChannelCapabilityProfile) and any(
                not set(channel_set) <= set(self.topology.channels)
                for channel_set in feature.profile.supported_channel_sets
            ):
                raise ValueError(
                    "source cross-channel profile references an unknown channel"
                )
        if not isinstance(self.query_contract, SourceQueryContract):
            raise ValueError("source descriptor query_contract has an invalid type")
        if not isinstance(self.safety_profile, SourceSafetyProfile):
            raise ValueError("source descriptor safety_profile has an invalid type")


class SnapshotConsistencyState(StrEnum):
    CONSISTENT = "consistent"
    DRIFTED = "drifted"
    UNPROVEN = "unproven"


@dataclass(frozen=True, slots=True)
class SourceSnapshotConsistency:
    state: SnapshotConsistencyState
    session_epoch: str
    anchor_fields: tuple[SourceFieldRef, ...]
    anchor_digest_before: str
    anchor_digest_after: str | None
    device_revision_token_before: str | None
    device_revision_token_after: str | None
    reason_code: SourceReasonCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, SnapshotConsistencyState):
            raise ValueError("source snapshot consistency state has an invalid type")
        _require_token(self.session_epoch, "source snapshot consistency session_epoch")
        if not isinstance(self.anchor_fields, tuple) or not self.anchor_fields or any(
            not isinstance(item, SourceFieldRef) for item in self.anchor_fields
        ):
            raise ValueError("source snapshot consistency anchor_fields have an invalid type")
        anchor_keys = tuple(
            (item.field.value, source_v2_canonical_json(item.target))
            for item in self.anchor_fields
        )
        if len(set(anchor_keys)) != len(anchor_keys) or tuple(sorted(anchor_keys)) != anchor_keys:
            raise ValueError("source snapshot consistency anchor_fields must be sorted and unique")
        if _SHA256.fullmatch(self.anchor_digest_before) is None:
            raise ValueError("source snapshot anchor_digest_before has an invalid format")
        if self.anchor_digest_after is not None and _SHA256.fullmatch(
            self.anchor_digest_after
        ) is None:
            raise ValueError("source snapshot anchor_digest_after has an invalid format")
        for label, value in (
            ("device_revision_token_before", self.device_revision_token_before),
            ("device_revision_token_after", self.device_revision_token_after),
        ):
            if value is not None:
                _require_token(value, f"source snapshot consistency {label}")
        if self.state is SnapshotConsistencyState.CONSISTENT:
            if self.anchor_digest_after != self.anchor_digest_before:
                raise ValueError("CONSISTENT snapshots require matching anchor digests")
            if (
                self.device_revision_token_before is not None
                and self.device_revision_token_after is not None
                and self.device_revision_token_before != self.device_revision_token_after
            ):
                raise ValueError("CONSISTENT snapshots require matching device revision tokens")
            if self.reason_code is not None:
                raise ValueError("CONSISTENT snapshots cannot carry a reason_code")
        elif not isinstance(self.reason_code, SourceReasonCode):
            raise ValueError("non-CONSISTENT snapshots require a registered reason_code")


@dataclass(frozen=True, slots=True)
class SourceSnapshotV2:
    snapshot_id: str
    context_id: str
    correlation_id: str
    captured_at_utc: str
    runtime_profile: SourceRuntimeCapabilityProfile
    channels: tuple[SourceChannelStateV2, ...]
    system: Observed[SourceSystemStateV2]
    cross_channel: Observed[SourceCrossChannelStateV2]
    consistency: SourceSnapshotConsistency
    plan_digest: str
    query_count: int
    session_health_before: str
    session_health_after: str

    def __post_init__(self) -> None:
        _require_token(self.snapshot_id, "source snapshot_id")
        _require_token(self.context_id, "source snapshot context_id")
        _require_token(self.correlation_id, "source snapshot correlation_id")
        _parse_utc_timestamp(self.captured_at_utc, "source snapshot captured_at_utc")
        if not isinstance(self.runtime_profile, SourceRuntimeCapabilityProfile):
            raise ValueError("source snapshot runtime_profile has an invalid type")
        if not isinstance(self.channels, tuple) or not self.channels or any(
            not isinstance(item, SourceChannelStateV2) for item in self.channels
        ):
            raise ValueError("source snapshot channels have an invalid type")
        channel_ids = tuple(item.channel for item in self.channels)
        if len(set(channel_ids)) != len(channel_ids) or tuple(sorted(channel_ids)) != channel_ids:
            raise ValueError("source snapshot channels must be sorted and unique")
        _require_observed(self.system, "source snapshot system")
        _require_observed(self.cross_channel, "source snapshot cross_channel")
        if not isinstance(self.consistency, SourceSnapshotConsistency):
            raise ValueError("source snapshot consistency has an invalid type")
        if _SHA256.fullmatch(self.plan_digest) is None:
            raise ValueError("source snapshot plan_digest has an invalid format")
        _require_int(self.query_count, "source snapshot query_count", minimum=0)
        _require_token(self.session_health_before, "source snapshot session_health_before")
        _require_token(self.session_health_after, "source snapshot session_health_after")

    def as_dict(self) -> dict[str, object]:
        return source_snapshot_v2_document(self)


@runtime_checkable
class SourceSnapshotV2Driver(InstrumentDriver, Protocol):
    def execute_source_query_plan_v2(
        self,
        plan: SourceSemanticQueryPlan,
    ) -> SourceQueryExecutionRecord: ...


@runtime_checkable
class SourceBasicConfigureV2Driver(InstrumentDriver, Protocol):
    def configure_source_basic_v2(
        self,
        request: SourceBasicConfigureRequest,
    ) -> SourceBasicConfigureResult: ...


@runtime_checkable
class SourceHarmonicConfigureV2Driver(InstrumentDriver, Protocol):
    def configure_source_harmonics_v2(
        self,
        request: SourceHarmonicConfigureRequest,
    ) -> SourceHarmonicConfigureResult: ...


@runtime_checkable
class SourceOutputV2Driver(InstrumentDriver, Protocol):
    def set_source_output_v2(
        self,
        request: SourceOutputRequest,
    ) -> SourceOutputResult: ...


def source_v2_to_data(value: object) -> object:
    """Convert Source V2 public values into strict JSON-compatible data."""

    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Source V2 JSON cannot contain non-finite floats")
        return value
    if isinstance(value, tuple):
        return [source_v2_to_data(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("Source V2 JSON object keys must be strings")
        return {key: source_v2_to_data(value[key]) for key in sorted(value)}
    if is_dataclass(value) and not isinstance(value, type):
        payload: dict[str, object] = {"type": type(value).__name__}
        for item in fields(value):
            payload[item.name] = source_v2_to_data(getattr(value, item.name))
        return payload
    raise TypeError(f"unsupported Source V2 JSON value: {type(value).__name__}")


def source_v2_canonical_json(value: object) -> str:
    return json.dumps(
        source_v2_to_data(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def source_v2_digest(value: object) -> str:
    return "sha256:" + sha256(source_v2_canonical_json(value).encode("utf-8")).hexdigest()


def source_snapshot_v2_document(snapshot: SourceSnapshotV2) -> dict[str, object]:
    if not isinstance(snapshot, SourceSnapshotV2):
        raise TypeError("snapshot must be SourceSnapshotV2")
    data = source_v2_to_data(snapshot)
    assert isinstance(data, dict)
    return {
        "schema": SOURCE_SNAPSHOT_SCHEMA,
        **data,
    }


def source_snapshot_v2_operation_artifact(snapshot: SourceSnapshotV2) -> dict[str, object]:
    """Build the read-only operation artifact without driver-private records."""

    if not isinstance(snapshot, SourceSnapshotV2):
        raise TypeError("snapshot must be SourceSnapshotV2")
    return {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.snapshot_v2",
        "context_id": snapshot.context_id,
        "correlation_id": snapshot.correlation_id,
        "session_epoch": snapshot.consistency.session_epoch,
        "capability_decision": {
            "capability": "source.snapshot_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": snapshot.runtime_profile.descriptor_digest,
        },
        "snapshot": source_snapshot_v2_document(snapshot),
        "query": {
            "effect": SourceQueryEffect.PURE_READ.value,
            "plan_digest": snapshot.plan_digest,
            "query_count": snapshot.query_count,
        },
        "session_health": {
            "before": snapshot.session_health_before,
            "after": snapshot.session_health_after,
        },
        "final_state": {
            "consistency": snapshot.consistency.state.value,
            "session_health": snapshot.session_health_after,
        },
        "evidence_refs": sorted(
            {
                ref
                for feature in snapshot.runtime_profile.features
                for ref in feature.evidence_refs
            }
        ),
    }


def source_snapshot_timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "SOURCE_CONTRACT_VERSION",
    "SOURCE_OPERATION_ARTIFACT_SCHEMA",
    "SOURCE_SNAPSHOT_MIN_CORE_VERSION",
    "SOURCE_SNAPSHOT_SCHEMA",
    "ArbitraryFacet",
    "Availability",
    "BasicWaveFacet",
    "BudgetEvidenceSource",
    "BudgetProofStrength",
    "BurstFacet",
    "ClosedFloatInterval",
    "ComponentAmplitudeKind",
    "CompositeOutputBudget",
    "HarmonicCompleteness",
    "HarmonicFacet",
    "ModulationFacet",
    "Observed",
    "OutputFacet",
    "PortVoltageBounds",
    "PulseFacet",
    "ResistanceBounds",
    "SnapshotConsistencyState",
    "SourceActivationPredicate",
    "SourceActivationRule",
    "SourceAmplitude",
    "SourceAmplitudeUnit",
    "SourceAnchorField",
    "SourceAffectedClosure",
    "SourceArbitraryCapabilityProfile",
    "SourceArbitraryOvershootConstraint",
    "SourceArbitraryPlaybackMode",
    "SourceBasicCapabilityProfile",
    "SourceBudgetBlockerCode",
    "SourceBurstCapabilityProfile",
    "SourceBurstMode",
    "SourceCascadeState",
    "SourceChannelStateV2",
    "SourceClockSyncCapabilityProfile",
    "SourceComponentAmplitude",
    "SourceConstraintApplicability",
    "SourceCounterCapabilityProfile",
    "SourceCounterInputState",
    "SourceCounterMeasurementKind",
    "SourceCounterMeasurementV2",
    "SourceCrossChannelCapabilityProfile",
    "SourceCrossChannelStateV2",
    "SourceDescriptorExtensions",
    "SourceDisplayLoad",
    "SourceEnergyEffect",
    "SourceFacetQueryContract",
    "SourceFacetScope",
    "SourceFeature",
    "SourceFeatureCapability",
    "SourceFeatureDirection",
    "SourceFeatureProfile",
    "SourceFieldId",
    "SourceFieldRef",
    "SourceFrequencyDeratingBand",
    "SourceFrequencyDeratingConstraint",
    "SourceFrequencyMode",
    "SourceGatePolarity",
    "SourceHarmonicCapabilityProfile",
    "SourceHarmonicComponentV2",
    "SourceInputCoupling",
    "SourceLoadKind",
    "SourceModulationCapabilityProfile",
    "SourceModulationEnvelopeConstraint",
    "SourceModulationKind",
    "SourceModulationParameter",
    "SourceModulationParameterKind",
    "SourceModulationSource",
    "SourceNoisePeakConstraint",
    "SourceOperationContract",
    "SourceOutputCapabilityProfile",
    "SourceOutputPolarity",
    "SourceProtocolQueryRecord",
    "SourcePulseCapabilityProfile",
    "SourcePulseHoldBasis",
    "SourceQueryContract",
    "SourceQueryEffect",
    "SourceQueryExecutionRecord",
    "SourceQueryItemOutcome",
    "SourceQueryPhase",
    "SourceReasonCode",
    "SourceReferenceClockMode",
    "SourceReferenceClockState",
    "SourceRelationEdge",
    "SourceRelationGraph",
    "SourceRelationState",
    "SourceResistanceConstraint",
    "SourceRuntimeCapabilityProfile",
    "SourceRuntimeIdentity",
    "SourceSafetyConstraint",
    "SourceSafetyConstraintKind",
    "SourceSafetyConstraintProfile",
    "SourceSafetyProfile",
    "SafetyContributor",
    "SourceScopeRef",
    "SourceSemanticQueryItem",
    "SourceSemanticQueryPlan",
    "SourceSharedPowerConstraint",
    "SourceSharedPowerBudget",
    "SourceSharedPowerState",
    "SourceSignalPathKind",
    "SourceSnapshotConsistency",
    "SourceSnapshotV2",
    "SourceSnapshotV2Driver",
    "SourceSweepCapabilityProfile",
    "SourceSweepMarker",
    "SourceSweepSpacing",
    "SourceSyncState",
    "SourceTerminationEvidence",
    "SourceStorageEffect",
    "SourceSystemStateV2",
    "SourceTopologyContract",
    "SourceTriggerOutput",
    "SourceTriggerSlope",
    "SourceTriggerSource",
    "SourceTriggerState",
    "SourceTypedObservation",
    "SourceVoltageReferenceConstraint",
    "SourceV1WriteRouteId",
    "SourceWaveformKind",
    "SupportState",
    "SweepFacet",
    "TerminationEvidenceLifetime",
    "TerminationEvidenceSource",
    "TerminationKind",
    "TerminationSpec",
    "VoltageReferenceBasis",
    "source_snapshot_v2_document",
    "source_v2_canonical_json",
    "source_v2_digest",
    "source_v2_to_data",
    "PatchAction",
    "PatchMode",
    "PatchValue",
    "SourceBasicPatch",
    "SourceBasicConfigureRequest",
    "SourceBasicConfigureResult",
    "SourceBasicConfigureV2Driver",
    "SourceOutputRequest",
    "SourceOutputResult",
    "SourceOutputV2Driver",
    "SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT",
    "SOURCE_OUTPUT_ENABLE_V2_OPERATION_CONTRACT",
    "SOURCE_OUTPUT_DISABLE_V2_OPERATION_CONTRACT",
    "SourceHarmonicPreset",
    "SourceHarmonicConfigureRequest",
    "SourceHarmonicConfigureResult",
    "SourceHarmonicConfigureV2Driver",
    "SOURCE_HARMONICS_CONFIGURE_V2_OPERATION_CONTRACT",
]
