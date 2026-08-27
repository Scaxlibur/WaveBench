"""Public contracts for RF signal-source plugins.

This module deliberately models radio-frequency sources independently from
the ``source`` domain used by function and arbitrary waveform generators.
It contains static descriptors, typed requests/results, and snapshots; it
never opens a transport or sends SCPI commands.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
import re
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar, runtime_checkable

from .contracts import InstrumentDriver


RF_SOURCE_CONTRACT_VERSION = "wavebench.rf_source.v1"
RF_SOURCE_SNAPSHOT_SCHEMA = "wavebench.rf_source.snapshot.v1"
RF_SOURCE_MODULATION_STATE_SCHEMA = "wavebench.rf_source.modulation_state.v1"
RF_SOURCE_MODULATION_SNAPSHOT_SCHEMA = "wavebench.rf_source.modulation_snapshot.v1"
RF_SOURCE_PULSE_SNAPSHOT_SCHEMA = "wavebench.rf_source.pulse_snapshot.v1"
RF_SOURCE_PULSE_OUTPUT_SNAPSHOT_SCHEMA = "wavebench.rf_source.pulse_output_snapshot.v1"
RF_SOURCE_SWEEP_SNAPSHOT_SCHEMA = "wavebench.rf_source.sweep_snapshot.v1"
RF_SOURCE_TRIGGER_SNAPSHOT_SCHEMA = "wavebench.rf_source.trigger_snapshot.v1"
RF_SOURCE_OPERATION_ARTIFACT_SCHEMA = "wavebench.rf_source.operation.v1"
RF_SOURCE_SNAPSHOT_MIN_CORE_VERSION = "0.8.25"

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


def _require_bool(value: object, label: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")


def _require_token(value: object, label: str) -> None:
    if not isinstance(value, str) or _SAFE_TOKEN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a short safe token")


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


def _require_integer(
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
    names = tuple(value.value for value in values)
    if len(set(names)) != len(names) or tuple(sorted(names)) != names:
        raise ValueError(f"{label} must be sorted by value and unique")


def _require_token_tuple(
    values: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{label} must be a tuple")
    if not allow_empty and not values:
        raise ValueError(f"{label} must not be empty")
    for value in values:
        _require_token(value, label)
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


class RfAvailability(StrEnum):
    VALUE = "value"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class RfReasonCode(StrEnum):
    DESCRIPTOR_UNSUPPORTED = "descriptor_unsupported"
    NOT_REQUESTED = "not_requested"
    RESPONSE_MISSING_FIELD = "response_missing_field"
    RESPONSE_INVALID_VALUE = "response_invalid_value"
    DRIVER_SKIPPED_OPTIONAL = "driver_skipped_optional"
    PROTOCOL_RECORD_INVALID = "protocol_record_invalid"
    SESSION_NOT_HEALTHY = "session_not_healthy"
    UNKNOWN_STATE = "unknown_state"


class RfModulationState(StrEnum):
    DISABLED = "disabled"
    ENABLED = "enabled"


class RfModulationKind(StrEnum):
    AM = "am"
    FM = "fm"
    PM = "pm"


class RfModulationSource(StrEnum):
    """Known current modulation-source values.

    M3 configuration profiles remain internal-only.  ``EXTERNAL`` exists only
    so a driver can report an inactive device state that M3 will explicitly
    replace with the requested internal source.
    """

    INTERNAL = "internal"
    EXTERNAL = "external"


class RfModulationWaveform(StrEnum):
    """Known current internal modulation-waveform values.

    M3 configuration profiles remain sine-only.  ``SQUARE`` exists only for
    typed readback of a current inactive device state before M3 replaces it.
    """

    SINE = "sine"
    SQUARE = "square"


class RfModulationValueUnit(StrEnum):
    PERCENT = "percent"
    HZ = "hz"
    RAD = "rad"


class RfPulseState(StrEnum):
    DISABLED = "disabled"
    ENABLED = "enabled"


class RfPulseSource(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class RfPulseMode(StrEnum):
    SINGLE = "single"
    TRAIN = "train"


class RfPulsePolarity(StrEnum):
    NORMAL = "normal"
    INVERTED = "inverted"


class RfPulseOutputDirection(StrEnum):
    """Physical direction declared by the bounded rear-panel Pulse contract."""

    OUTPUT = "output"


class RfPulseTriggerMode(StrEnum):
    """Logical Pulse trigger modes reported by a device configuration query."""

    AUTOMATIC = "automatic"
    BUS = "bus"
    EXTERNAL = "external"
    EXTERNAL_GATE = "external_gate"
    KEY = "key"


class RfExternalTriggerEdge(StrEnum):
    NEGATIVE = "negative"
    POSITIVE = "positive"


class RfExternalGatePolarity(StrEnum):
    INVERTED = "inverted"
    NORMAL = "normal"


class RfSweepState(StrEnum):
    DISABLED = "disabled"
    ENABLED = "enabled"


class RfSweepType(StrEnum):
    STEP = "step"


class RfSweepDirection(StrEnum):
    FORWARD = "forward"


class RfSweepShape(StrEnum):
    RAMP = "ramp"


class RfSweepSpacing(StrEnum):
    LINEAR = "linear"


class RfSweepMode(StrEnum):
    CONTINUOUS = "continuous"
    SINGLE = "single"


class RfSweepTriggerMode(StrEnum):
    AUTOMATIC = "automatic"
    BUS = "bus"
    EXTERNAL = "external"
    KEY = "key"


class RfFeature(StrEnum):
    CW = "cw"
    MODULATION = "modulation"
    MODULATED_OUTPUT = "modulated_output"
    OUTPUT = "output"
    PULSE = "pulse"
    PULSE_OUTPUT = "pulse_output"
    SWEEP = "sweep"
    TRIGGER = "trigger"


class RfFeatureDirection(StrEnum):
    ARM = "arm"
    CONFIGURE = "configure"
    DISABLE = "disable"
    ENABLE = "enable"
    FIRE = "fire"
    READ = "read"
    STOP = "stop"
    TRIGGER = "trigger"


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RfObserved(Generic[T]):
    """A typed value or a stable, non-sensitive reason why it is unavailable."""

    availability: RfAvailability
    value: T | None = None
    reason_code: RfReasonCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.availability, RfAvailability):
            raise ValueError("RF observation availability has an invalid type")
        if self.availability is RfAvailability.VALUE:
            if self.value is None:
                raise ValueError("VALUE RF observations must carry a value")
            if self.reason_code is not None:
                raise ValueError("VALUE RF observations cannot carry a reason_code")
            if _contains_nonfinite(self.value):
                raise ValueError("VALUE RF observations cannot contain non-finite floats")
        else:
            if self.value is not None:
                raise ValueError("non-VALUE RF observations cannot carry a value")
            if not isinstance(self.reason_code, RfReasonCode):
                raise ValueError("non-VALUE RF observations require a registered reason_code")

    @classmethod
    def value_of(cls, value: T) -> "RfObserved[T]":
        return cls(availability=RfAvailability.VALUE, value=value)

    @classmethod
    def missing(
        cls,
        availability: RfAvailability,
        reason_code: RfReasonCode,
    ) -> "RfObserved[T]":
        if availability is RfAvailability.VALUE:
            raise ValueError("missing RF observations cannot use VALUE")
        return cls(availability=availability, reason_code=reason_code)


@dataclass(frozen=True, slots=True)
class RfOutputPortProfile:
    port_id: str
    frequency_min_hz: float
    frequency_max_hz: float
    power_min_dbm: float
    power_max_dbm: float
    power_reference_impedance_ohm: float

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF output port_id")
        _require_finite(self.frequency_min_hz, "RF frequency_min_hz", minimum=0.0)
        _require_finite(
            self.frequency_max_hz,
            "RF frequency_max_hz",
            minimum=self.frequency_min_hz,
        )
        _require_finite(self.power_min_dbm, "RF power_min_dbm")
        _require_finite(
            self.power_max_dbm,
            "RF power_max_dbm",
            minimum=self.power_min_dbm,
        )
        _require_finite(
            self.power_reference_impedance_ohm,
            "RF power_reference_impedance_ohm",
            minimum=0.0,
        )
        if self.power_reference_impedance_ohm <= 0.0:
            raise ValueError("RF power_reference_impedance_ohm must be positive")


@dataclass(frozen=True, slots=True)
class RfSourceTopology:
    ports: tuple[RfOutputPortProfile, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.ports, tuple) or not self.ports or any(
            not isinstance(port, RfOutputPortProfile) for port in self.ports
        ):
            raise ValueError("RF source topology ports have an invalid type")
        port_ids = tuple(port.port_id for port in self.ports)
        if len(set(port_ids)) != len(port_ids) or tuple(sorted(port_ids)) != port_ids:
            raise ValueError("RF source topology ports must be sorted and unique")


@dataclass(frozen=True, slots=True)
class RfProtectionStatus:
    active_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_token_tuple(self.active_codes, "RF protection active_codes", allow_empty=True)


@dataclass(frozen=True, slots=True)
class RfProtectionConditionPolicy:
    code: str
    blocks_output_enable: bool

    def __post_init__(self) -> None:
        _require_token(self.code, "RF protection condition code")
        _require_bool(self.blocks_output_enable, "RF protection blocks_output_enable")


@dataclass(frozen=True, slots=True)
class RfPortSnapshot:
    port_id: str
    frequency_hz: RfObserved[float]
    power_dbm: RfObserved[float]
    output_enabled: RfObserved[bool]
    modulation: RfObserved[RfModulationState]
    pulse: RfObserved[RfPulseState]
    sweep: RfObserved[RfSweepState]

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF port snapshot port_id")
        _require_observed_number(self.frequency_hz, "RF port snapshot frequency_hz", minimum=0.0)
        _require_observed_number(self.power_dbm, "RF port snapshot power_dbm")
        _require_observed_value(self.output_enabled, bool, "RF port snapshot output_enabled")
        _require_observed_value(
            self.modulation,
            RfModulationState,
            "RF port snapshot modulation",
        )
        _require_observed_value(self.pulse, RfPulseState, "RF port snapshot pulse")
        _require_observed_value(self.sweep, RfSweepState, "RF port snapshot sweep")


@dataclass(frozen=True, slots=True)
class RfSourceSnapshot:
    ports: tuple[RfPortSnapshot, ...]
    protection: RfObserved[RfProtectionStatus]

    def __post_init__(self) -> None:
        if not isinstance(self.ports, tuple) or not self.ports or any(
            not isinstance(port, RfPortSnapshot) for port in self.ports
        ):
            raise ValueError("RF source snapshot ports have an invalid type")
        port_ids = tuple(port.port_id for port in self.ports)
        if len(set(port_ids)) != len(port_ids) or tuple(sorted(port_ids)) != port_ids:
            raise ValueError("RF source snapshot ports must be sorted and unique")
        _require_observed_value(
            self.protection,
            RfProtectionStatus,
            "RF source snapshot protection",
        )

    def as_dict(self) -> dict[str, object]:
        return rf_source_snapshot_document(self)


@dataclass(frozen=True, slots=True)
class RfModulationModeProfile:
    """One bounded, readable internal-sine modulation mode declaration."""

    kind: RfModulationKind
    value_unit: RfModulationValueUnit
    value_min: float
    value_max: float
    internal_frequency_min_hz: float
    internal_frequency_max_hz: float
    source: RfModulationSource = RfModulationSource.INTERNAL
    waveform: RfModulationWaveform = RfModulationWaveform.SINE

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RfModulationKind):
            raise ValueError("RF modulation mode kind has an invalid type")
        if not isinstance(self.value_unit, RfModulationValueUnit):
            raise ValueError("RF modulation mode value_unit has an invalid type")
        if not isinstance(self.source, RfModulationSource):
            raise ValueError("RF modulation mode source has an invalid type")
        if not isinstance(self.waveform, RfModulationWaveform):
            raise ValueError("RF modulation mode waveform has an invalid type")
        if self.source is not RfModulationSource.INTERNAL:
            raise ValueError("RF modulation mode profiles must use the internal source")
        if self.waveform is not RfModulationWaveform.SINE:
            raise ValueError("RF modulation mode profiles must use the sine waveform")
        _require_finite(self.value_min, "RF modulation mode value_min")
        _require_finite(
            self.value_max,
            "RF modulation mode value_max",
            minimum=self.value_min,
        )
        _require_finite(
            self.internal_frequency_min_hz,
            "RF modulation mode internal_frequency_min_hz",
            minimum=0.0,
        )
        _require_finite(
            self.internal_frequency_max_hz,
            "RF modulation mode internal_frequency_max_hz",
            minimum=self.internal_frequency_min_hz,
        )


@dataclass(frozen=True, slots=True)
class RfCwProfile:
    frequency_readable: bool
    power_readable: bool
    frequency_configurable: bool = False
    power_configurable: bool = False

    def __post_init__(self) -> None:
        _require_bool(self.frequency_readable, "RF CW frequency_readable")
        _require_bool(self.power_readable, "RF CW power_readable")
        _require_bool(self.frequency_configurable, "RF CW frequency_configurable")
        _require_bool(self.power_configurable, "RF CW power_configurable")
        if self.frequency_configurable and not self.frequency_readable:
            raise ValueError("RF configurable frequency requires readable frequency")
        if self.power_configurable and not self.power_readable:
            raise ValueError("RF configurable power requires readable power")


@dataclass(frozen=True, slots=True)
class RfOutputProfile:
    output_readable: bool

    def __post_init__(self) -> None:
        _require_bool(self.output_readable, "RF output output_readable")


@dataclass(frozen=True, slots=True)
class RfModulationProfile:
    state_readable: bool
    configuration_readable: bool = False
    mode_profiles: tuple[RfModulationModeProfile, ...] = ()

    def __post_init__(self) -> None:
        _require_bool(self.state_readable, "RF modulation state_readable")
        _require_bool(self.configuration_readable, "RF modulation configuration_readable")
        if not isinstance(self.mode_profiles, tuple) or any(
            not isinstance(profile, RfModulationModeProfile) for profile in self.mode_profiles
        ):
            raise ValueError("RF modulation mode_profiles have an invalid type")
        kinds = tuple(profile.kind for profile in self.mode_profiles)
        if len(set(kinds)) != len(kinds) or tuple(sorted(kinds, key=lambda item: item.value)) != kinds:
            raise ValueError("RF modulation mode_profiles must be sorted and unique")
        if self.configuration_readable and not self.state_readable:
            raise ValueError("RF modulation configuration readback requires readable state")


@dataclass(frozen=True, slots=True)
class RfModulatedOutputProfile:
    """Bounded profiles that may enable an already modulated RF output.

    This is separate from :class:`RfOutputProfile`: ordinary RF output control
    remains valid only while modulation is disabled. A descriptor must opt in
    only after independent evidence covers the active modulation state, output
    limits, and recovery behavior.
    """

    maximum_power_dbm: float
    mode_profiles: tuple[RfModulationModeProfile, ...]

    def __post_init__(self) -> None:
        _require_finite(self.maximum_power_dbm, "RF modulated-output maximum_power_dbm")
        if not isinstance(self.mode_profiles, tuple) or not self.mode_profiles or any(
            not isinstance(profile, RfModulationModeProfile) for profile in self.mode_profiles
        ):
            raise ValueError("RF modulated-output mode_profiles have an invalid type")
        kinds = tuple(profile.kind for profile in self.mode_profiles)
        if len(set(kinds)) != len(kinds) or tuple(sorted(kinds, key=lambda item: item.value)) != kinds:
            raise ValueError("RF modulated-output mode_profiles must be sorted and unique")


@dataclass(frozen=True, slots=True)
class RfPulseProfile:
    state_readable: bool
    configuration_readable: bool = False
    mode_profiles: tuple["RfPulseModeProfile", ...] = ()

    def __post_init__(self) -> None:
        _require_bool(self.state_readable, "RF pulse state_readable")
        _require_bool(self.configuration_readable, "RF pulse configuration_readable")
        if not isinstance(self.mode_profiles, tuple) or any(
            not isinstance(profile, RfPulseModeProfile) for profile in self.mode_profiles
        ):
            raise ValueError("RF pulse mode_profiles have an invalid type")
        identities = tuple((profile.source.value, profile.mode.value) for profile in self.mode_profiles)
        if len(set(identities)) != len(identities) or tuple(sorted(identities)) != identities:
            raise ValueError("RF pulse mode_profiles must be sorted and unique")
        if self.configuration_readable and not self.state_readable:
            raise ValueError("RF pulse configuration readback requires readable state")


@dataclass(frozen=True, slots=True)
class RfPulseModeProfile:
    """One bounded pulse profile that can be configured while RF remains OFF."""

    source: RfPulseSource
    mode: RfPulseMode
    polarities: tuple[RfPulsePolarity, ...]
    period_min_s: float
    period_max_s: float
    width_min_s: float
    width_max_s: float
    minimum_off_time_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.source, RfPulseSource):
            raise ValueError("RF pulse mode source has an invalid type")
        if not isinstance(self.mode, RfPulseMode):
            raise ValueError("RF pulse mode has an invalid type")
        if self.source is not RfPulseSource.INTERNAL:
            raise ValueError("RF pulse mode profiles must use the internal source")
        if self.mode is not RfPulseMode.SINGLE:
            raise ValueError("RF pulse mode profiles must use the single mode")
        _require_enum_tuple(self.polarities, RfPulsePolarity, "RF pulse mode polarities")
        _require_finite(self.period_min_s, "RF pulse mode period_min_s", minimum=0.0)
        _require_finite(
            self.period_max_s,
            "RF pulse mode period_max_s",
            minimum=self.period_min_s,
        )
        _require_finite(self.width_min_s, "RF pulse mode width_min_s", minimum=0.0)
        _require_finite(
            self.width_max_s,
            "RF pulse mode width_max_s",
            minimum=self.width_min_s,
        )
        _require_finite(
            self.minimum_off_time_s,
            "RF pulse mode minimum_off_time_s",
            minimum=0.0,
        )
        if self.minimum_off_time_s <= 0.0:
            raise ValueError("RF pulse mode minimum_off_time_s must be positive")
        if self.period_max_s < self.width_min_s + self.minimum_off_time_s:
            raise ValueError("RF pulse mode ranges cannot satisfy the minimum off time")


@dataclass(frozen=True, slots=True)
class RfPulseOutputProfile:
    """One bounded physical Pulse-output interface tied to an RF port.

    This contract intentionally models only a proven output direction.  A
    connector with an ``IN/OUT`` label does not imply that input behavior is
    declared.  The profile fixes both the documented electrical characteristics
    and the exact internal Pulse profile that must be read back before the
    physical output can be enabled.
    """

    interface_id: str
    direction: RfPulseOutputDirection
    output_readable: bool
    low_level_v: float
    high_level_v: float
    output_impedance_ohm: float
    source: RfPulseSource
    mode: RfPulseMode
    period_s: float
    width_s: float
    polarity: RfPulsePolarity
    pulse_state: RfPulseState = RfPulseState.DISABLED

    def __post_init__(self) -> None:
        _require_token(self.interface_id, "RF pulse-output interface_id")
        if not isinstance(self.direction, RfPulseOutputDirection):
            raise ValueError("RF pulse-output direction has an invalid type")
        if self.direction is not RfPulseOutputDirection.OUTPUT:
            raise ValueError("RF pulse-output profiles must declare output direction")
        _require_bool(self.output_readable, "RF pulse-output output_readable")
        _require_finite(self.low_level_v, "RF pulse-output low_level_v", minimum=0.0)
        _require_finite(
            self.high_level_v,
            "RF pulse-output high_level_v",
            minimum=self.low_level_v,
        )
        if self.high_level_v <= self.low_level_v:
            raise ValueError("RF pulse-output high_level_v must exceed low_level_v")
        _require_finite(
            self.output_impedance_ohm,
            "RF pulse-output output_impedance_ohm",
            minimum=0.0,
        )
        if self.output_impedance_ohm <= 0.0:
            raise ValueError("RF pulse-output output_impedance_ohm must be positive")
        if self.source is not RfPulseSource.INTERNAL:
            raise ValueError("RF pulse-output profiles must use the internal source")
        if self.mode is not RfPulseMode.SINGLE:
            raise ValueError("RF pulse-output profiles must use the single mode")
        _require_finite(self.period_s, "RF pulse-output period_s", minimum=0.0)
        _require_finite(self.width_s, "RF pulse-output width_s", minimum=0.0)
        if self.period_s <= 0.0 or self.width_s <= 0.0:
            raise ValueError("RF pulse-output period_s and width_s must be positive")
        if self.width_s >= self.period_s:
            raise ValueError("RF pulse-output width_s must be less than period_s")
        if not isinstance(self.polarity, RfPulsePolarity):
            raise ValueError("RF pulse-output polarity has an invalid type")
        if self.pulse_state is not RfPulseState.DISABLED:
            raise ValueError("RF pulse-output profiles must keep Pulse modulation disabled")


@dataclass(frozen=True, slots=True)
class RfSweepProfile:
    state_readable: bool
    configuration_readable: bool = False
    mode_profiles: tuple["RfSweepModeProfile", ...] = ()

    def __post_init__(self) -> None:
        _require_bool(self.state_readable, "RF sweep state_readable")
        _require_bool(self.configuration_readable, "RF sweep configuration_readable")
        if not isinstance(self.mode_profiles, tuple) or any(
            not isinstance(profile, RfSweepModeProfile) for profile in self.mode_profiles
        ):
            raise ValueError("RF sweep mode_profiles have an invalid type")
        identities = tuple(
            (
                profile.sweep_type.value,
                profile.direction.value,
                profile.shape.value,
                profile.spacing.value,
            )
            for profile in self.mode_profiles
        )
        if len(set(identities)) != len(identities) or tuple(sorted(identities)) != identities:
            raise ValueError("RF sweep mode_profiles must be sorted and unique")
        if self.configuration_readable and not self.state_readable:
            raise ValueError("RF sweep configuration readback requires readable state")


@dataclass(frozen=True, slots=True)
class RfSweepModeProfile:
    """One bounded frequency-only Step Sweep profile that remains disabled."""

    sweep_type: RfSweepType
    direction: RfSweepDirection
    shape: RfSweepShape
    spacing: RfSweepSpacing
    frequency_min_hz: float
    frequency_max_hz: float
    points_min: int
    points_max: int
    dwell_min_s: float
    dwell_max_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.sweep_type, RfSweepType):
            raise ValueError("RF sweep mode type has an invalid type")
        if not isinstance(self.direction, RfSweepDirection):
            raise ValueError("RF sweep mode direction has an invalid type")
        if not isinstance(self.shape, RfSweepShape):
            raise ValueError("RF sweep mode shape has an invalid type")
        if not isinstance(self.spacing, RfSweepSpacing):
            raise ValueError("RF sweep mode spacing has an invalid type")
        if self.sweep_type is not RfSweepType.STEP:
            raise ValueError("RF sweep mode profiles must use Step Sweep")
        if self.direction is not RfSweepDirection.FORWARD:
            raise ValueError("RF sweep mode profiles must use the forward direction")
        if self.shape is not RfSweepShape.RAMP:
            raise ValueError("RF sweep mode profiles must use ramp shape")
        if self.spacing is not RfSweepSpacing.LINEAR:
            raise ValueError("RF sweep mode profiles must use linear spacing")
        _require_finite(self.frequency_min_hz, "RF sweep mode frequency_min_hz", minimum=0.0)
        _require_finite(
            self.frequency_max_hz,
            "RF sweep mode frequency_max_hz",
            minimum=self.frequency_min_hz,
        )
        _require_integer(self.points_min, "RF sweep mode points_min", minimum=2)
        _require_integer(
            self.points_max,
            "RF sweep mode points_max",
            minimum=self.points_min,
        )
        _require_finite(self.dwell_min_s, "RF sweep mode dwell_min_s", minimum=0.0)
        _require_finite(
            self.dwell_max_s,
            "RF sweep mode dwell_max_s",
            minimum=self.dwell_min_s,
        )
        if self.dwell_min_s <= 0.0:
            raise ValueError("RF sweep mode dwell_min_s must be positive")


@dataclass(frozen=True, slots=True)
class RfTriggerProfile:
    """Complete read-only trigger-configuration profile for one RF output.

    The profile describes logical Pulse and Sweep trigger configuration that a
    driver can read.  It deliberately does not describe a physical trigger or
    sync connector, its direction, or electrical characteristics; those need a
    separate A5 physical-interface contract before any write or fire operation.
    """

    state_readable: bool
    pulse_trigger_modes: tuple[RfPulseTriggerMode, ...] = ()
    pulse_external_trigger_edges: tuple[RfExternalTriggerEdge, ...] = ()
    pulse_external_gate_polarities: tuple[RfExternalGatePolarity, ...] = ()
    sweep_modes: tuple[RfSweepMode, ...] = ()
    sweep_period_trigger_modes: tuple[RfSweepTriggerMode, ...] = ()
    sweep_point_trigger_modes: tuple[RfSweepTriggerMode, ...] = ()

    def __post_init__(self) -> None:
        _require_bool(self.state_readable, "RF trigger state_readable")
        fields = (
            (self.pulse_trigger_modes, RfPulseTriggerMode, "RF trigger pulse_trigger_modes"),
            (
                self.pulse_external_trigger_edges,
                RfExternalTriggerEdge,
                "RF trigger pulse_external_trigger_edges",
            ),
            (
                self.pulse_external_gate_polarities,
                RfExternalGatePolarity,
                "RF trigger pulse_external_gate_polarities",
            ),
            (self.sweep_modes, RfSweepMode, "RF trigger sweep_modes"),
            (
                self.sweep_period_trigger_modes,
                RfSweepTriggerMode,
                "RF trigger sweep_period_trigger_modes",
            ),
            (
                self.sweep_point_trigger_modes,
                RfSweepTriggerMode,
                "RF trigger sweep_point_trigger_modes",
            ),
        )
        for values, enum_type, label in fields:
            _require_enum_tuple(values, enum_type, label, allow_empty=not self.state_readable)
        if not self.state_readable and any(values for values, _, _ in fields):
            raise ValueError("RF unreadable trigger profile cannot declare trigger states")


RfFeatureProfile: TypeAlias = (
    RfCwProfile
    | RfOutputProfile
    | RfModulationProfile
    | RfModulatedOutputProfile
    | RfPulseProfile
    | RfPulseOutputProfile
    | RfSweepProfile
    | RfTriggerProfile
)

_FEATURE_PROFILE_TYPES: dict[RfFeature, type[RfFeatureProfile]] = {
    RfFeature.CW: RfCwProfile,
    RfFeature.MODULATION: RfModulationProfile,
    RfFeature.MODULATED_OUTPUT: RfModulatedOutputProfile,
    RfFeature.OUTPUT: RfOutputProfile,
    RfFeature.PULSE: RfPulseProfile,
    RfFeature.PULSE_OUTPUT: RfPulseOutputProfile,
    RfFeature.SWEEP: RfSweepProfile,
    RfFeature.TRIGGER: RfTriggerProfile,
}


@dataclass(frozen=True, slots=True)
class RfFeatureCapability:
    feature: RfFeature
    directions: tuple[RfFeatureDirection, ...]
    port_ids: tuple[str, ...]
    profile: RfFeatureProfile

    def __post_init__(self) -> None:
        if not isinstance(self.feature, RfFeature):
            raise ValueError("RF feature has an invalid type")
        _require_enum_tuple(self.directions, RfFeatureDirection, "RF feature directions")
        _require_token_tuple(self.port_ids, "RF feature port_ids")
        if not isinstance(self.profile, _FEATURE_PROFILE_TYPES[self.feature]):
            raise ValueError("RF feature profile does not match feature")


@dataclass(frozen=True, slots=True)
class RfSourceDescriptorExtensions:
    contract_version: Literal["wavebench.rf_source.v1"]
    topology: RfSourceTopology
    features: tuple[RfFeatureCapability, ...] = ()
    protection_conditions: tuple[RfProtectionConditionPolicy, ...] = ()

    def __post_init__(self) -> None:
        if self.contract_version != RF_SOURCE_CONTRACT_VERSION:
            raise ValueError("RF source descriptor contract_version is unsupported")
        if not isinstance(self.topology, RfSourceTopology):
            raise ValueError("RF source descriptor topology has an invalid type")
        if not isinstance(self.features, tuple) or any(
            not isinstance(feature, RfFeatureCapability) for feature in self.features
        ):
            raise ValueError("RF source descriptor features have an invalid type")
        feature_names = tuple(feature.feature.value for feature in self.features)
        if len(set(feature_names)) != len(feature_names) or tuple(sorted(feature_names)) != feature_names:
            raise ValueError("RF source descriptor features must be sorted and unique")
        topology_port_ids = {port.port_id for port in self.topology.ports}
        if any(not set(feature.port_ids) <= topology_port_ids for feature in self.features):
            raise ValueError("RF source descriptor feature references an unknown port")
        if not isinstance(self.protection_conditions, tuple) or any(
            not isinstance(condition, RfProtectionConditionPolicy)
            for condition in self.protection_conditions
        ):
            raise ValueError("RF source descriptor protection_conditions have an invalid type")
        condition_codes = tuple(condition.code for condition in self.protection_conditions)
        if len(set(condition_codes)) != len(condition_codes) or tuple(sorted(condition_codes)) != condition_codes:
            raise ValueError("RF source descriptor protection_conditions must be sorted and unique")


@dataclass(frozen=True, slots=True)
class RfCwRequest:
    """One OFF-only CW update for one explicitly addressed RF output port."""

    port_id: str
    frequency_hz: float | None = None
    power_dbm: float | None = None

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF CW request port_id")
        frequency_requested = self.frequency_hz is not None
        power_requested = self.power_dbm is not None
        if frequency_requested == power_requested:
            raise ValueError("RF CW request must set exactly one of frequency_hz or power_dbm")
        if frequency_requested:
            _require_finite(
                self.frequency_hz,
                "RF CW request frequency_hz",
                minimum=0.0,
            )
        if power_requested:
            _require_finite(self.power_dbm, "RF CW request power_dbm")


@dataclass(frozen=True, slots=True)
class RfCwResult:
    """A single CW field confirmed by an independent postcondition snapshot."""

    port_id: str
    frequency_hz: float | None = None
    power_dbm: float | None = None

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF CW result port_id")
        frequency_confirmed = self.frequency_hz is not None
        power_confirmed = self.power_dbm is not None
        if frequency_confirmed == power_confirmed:
            raise ValueError("RF CW result must confirm exactly one of frequency_hz or power_dbm")
        if frequency_confirmed:
            _require_finite(
                self.frequency_hz,
                "RF CW result frequency_hz",
                minimum=0.0,
            )
        if power_confirmed:
            _require_finite(self.power_dbm, "RF CW result power_dbm")


def _validate_modulation_fields(
    *,
    kind: RfModulationKind,
    depth_percent: float | None,
    frequency_deviation_hz: float | None,
    phase_deviation_rad: float | None,
    label: str,
) -> None:
    if not isinstance(kind, RfModulationKind):
        raise ValueError(f"{label} kind has an invalid type")
    fields = {
        RfModulationKind.AM: depth_percent,
        RfModulationKind.FM: frequency_deviation_hz,
        RfModulationKind.PM: phase_deviation_rad,
    }
    if sum(value is not None for value in fields.values()) != 1 or fields[kind] is None:
        raise ValueError(f"{label} must set exactly the parameter for its modulation kind")
    for field, value in fields.items():
        if value is not None:
            _require_finite(value, f"{label} {field.value} value", minimum=0.0)


@dataclass(frozen=True, slots=True)
class RfModulationRequest:
    """One bounded internal-sine AM, FM, or PM configuration for one RF port."""

    port_id: str
    kind: RfModulationKind
    internal_frequency_hz: float
    depth_percent: float | None = None
    frequency_deviation_hz: float | None = None
    phase_deviation_rad: float | None = None

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF modulation request port_id")
        _require_finite(
            self.internal_frequency_hz,
            "RF modulation request internal_frequency_hz",
            minimum=0.0,
        )
        _validate_modulation_fields(
            kind=self.kind,
            depth_percent=self.depth_percent,
            frequency_deviation_hz=self.frequency_deviation_hz,
            phase_deviation_rad=self.phase_deviation_rad,
            label="RF modulation request",
        )

    @property
    def value(self) -> float:
        value = {
            RfModulationKind.AM: self.depth_percent,
            RfModulationKind.FM: self.frequency_deviation_hz,
            RfModulationKind.PM: self.phase_deviation_rad,
        }[self.kind]
        assert value is not None
        return value

    @property
    def value_unit(self) -> RfModulationValueUnit:
        return {
            RfModulationKind.AM: RfModulationValueUnit.PERCENT,
            RfModulationKind.FM: RfModulationValueUnit.HZ,
            RfModulationKind.PM: RfModulationValueUnit.RAD,
        }[self.kind]


@dataclass(frozen=True, slots=True)
class RfModulationResult:
    """An internal-sine modulation request confirmed by typed readback."""

    port_id: str
    kind: RfModulationKind
    internal_frequency_hz: float
    depth_percent: float | None = None
    frequency_deviation_hz: float | None = None
    phase_deviation_rad: float | None = None

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF modulation result port_id")
        _require_finite(
            self.internal_frequency_hz,
            "RF modulation result internal_frequency_hz",
            minimum=0.0,
        )
        _validate_modulation_fields(
            kind=self.kind,
            depth_percent=self.depth_percent,
            frequency_deviation_hz=self.frequency_deviation_hz,
            phase_deviation_rad=self.phase_deviation_rad,
            label="RF modulation result",
        )

    @property
    def value(self) -> float:
        value = {
            RfModulationKind.AM: self.depth_percent,
            RfModulationKind.FM: self.frequency_deviation_hz,
            RfModulationKind.PM: self.phase_deviation_rad,
        }[self.kind]
        assert value is not None
        return value

    @property
    def value_unit(self) -> RfModulationValueUnit:
        return {
            RfModulationKind.AM: RfModulationValueUnit.PERCENT,
            RfModulationKind.FM: RfModulationValueUnit.HZ,
            RfModulationKind.PM: RfModulationValueUnit.RAD,
        }[self.kind]


@dataclass(frozen=True, slots=True)
class RfModulatedOutputRequest:
    """Enable one RF output only for an exactly read-back modulation profile.

    The operation never configures modulation. The embedded request is the
    caller's explicit assertion of the already active internal-sine profile
    that must be read back before and after the one RF-ON write.
    """

    modulation: RfModulationRequest

    def __post_init__(self) -> None:
        if not isinstance(self.modulation, RfModulationRequest):
            raise ValueError("RF modulated-output request requires RfModulationRequest")

    @property
    def port_id(self) -> str:
        return self.modulation.port_id

    @property
    def kind(self) -> RfModulationKind:
        return self.modulation.kind


@dataclass(frozen=True, slots=True)
class RfModulatedOutputResult:
    """A modulated RF-output enable confirmed by independent readback."""

    modulation: RfModulationResult
    write_completed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.modulation, RfModulationResult):
            raise ValueError("RF modulated-output result requires RfModulationResult")
        _require_bool(self.write_completed, "RF modulated-output result write_completed")
        if self.write_completed is not True:
            raise ValueError("RF modulated-output enable must complete one RF-ON write")

    @property
    def port_id(self) -> str:
        return self.modulation.port_id

    @property
    def kind(self) -> RfModulationKind:
        return self.modulation.kind


@dataclass(frozen=True, slots=True)
class RfModulationDisableRequest:
    """Disable exactly one active modulation mode on one RF output port.

    This request deliberately identifies the mode that must be active before
    the write.  It is not a broad reset: Service preflight rejects an unknown,
    mixed-mode, or otherwise unsafe modulation state before the driver sends
    its mode-specific disable sequence.
    """

    port_id: str
    kind: RfModulationKind

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF modulation disable request port_id")
        if not isinstance(self.kind, RfModulationKind):
            raise ValueError("RF modulation disable request kind has an invalid type")


@dataclass(frozen=True, slots=True)
class RfModulationDisableResult:
    """A mode-specific modulation-disable request confirmed by typed state readback."""

    port_id: str
    kind: RfModulationKind
    write_completed: bool

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF modulation disable result port_id")
        if not isinstance(self.kind, RfModulationKind):
            raise ValueError("RF modulation disable result kind has an invalid type")
        _require_bool(self.write_completed, "RF modulation disable result write_completed")


@dataclass(frozen=True, slots=True)
class RfModulationStateSnapshot:
    """State-only readback used before an M3 configuration write.

    An inactive device can legitimately retain an external source, a non-sine
    waveform, or source-dependent values that are not queryable.  M3 only
    needs its mode/global/fault state before it replaces that configuration,
    so this snapshot deliberately excludes profile fields.
    """

    port_id: str
    enabled_modes: tuple[RfModulationKind, ...] = ()
    global_enabled: bool = False
    fault_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF modulation state snapshot port_id")
        _require_enum_tuple(
            self.enabled_modes,
            RfModulationKind,
            "RF modulation state snapshot enabled_modes",
            allow_empty=True,
        )
        _require_bool(self.global_enabled, "RF modulation state snapshot global_enabled")
        _require_token_tuple(
            self.fault_codes,
            "RF modulation state snapshot fault_codes",
            allow_empty=True,
        )


@dataclass(frozen=True, slots=True)
class RfModulationSnapshot:
    """Complete typed readback for one internal-sine modulation mode.

    ``kind`` identifies the stored profile queried by the driver.  On devices
    with one shared FM/PM front-panel selection, ``selected_fm_pm_kind`` records
    that current selection separately.  This lets an OFF-only transaction
    safely prepare PM while FM is currently selected, then require the target
    selection in its postcondition.
    """

    port_id: str
    kind: RfModulationKind
    source: RfModulationSource
    waveform: RfModulationWaveform
    internal_frequency_hz: float
    selected_fm_pm_kind: RfModulationKind | None = None
    depth_percent: float | None = None
    frequency_deviation_hz: float | None = None
    phase_deviation_rad: float | None = None
    enabled_modes: tuple[RfModulationKind, ...] = ()
    global_enabled: bool = False
    fault_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF modulation snapshot port_id")
        if not isinstance(self.source, RfModulationSource):
            raise ValueError("RF modulation snapshot source has an invalid type")
        if not isinstance(self.waveform, RfModulationWaveform):
            raise ValueError("RF modulation snapshot waveform has an invalid type")
        _require_finite(
            self.internal_frequency_hz,
            "RF modulation snapshot internal_frequency_hz",
            minimum=0.0,
        )
        _validate_modulation_fields(
            kind=self.kind,
            depth_percent=self.depth_percent,
            frequency_deviation_hz=self.frequency_deviation_hz,
            phase_deviation_rad=self.phase_deviation_rad,
            label="RF modulation snapshot",
        )
        if self.kind is RfModulationKind.AM:
            if self.selected_fm_pm_kind is not None:
                raise ValueError("AM modulation snapshots cannot carry an FM/PM selection")
        elif self.selected_fm_pm_kind not in {
            RfModulationKind.FM,
            RfModulationKind.PM,
        }:
            raise ValueError("FM/PM modulation snapshots require a selected FM/PM kind")
        _require_enum_tuple(
            self.enabled_modes,
            RfModulationKind,
            "RF modulation snapshot enabled_modes",
            allow_empty=True,
        )
        _require_bool(self.global_enabled, "RF modulation snapshot global_enabled")
        _require_token_tuple(self.fault_codes, "RF modulation snapshot fault_codes", allow_empty=True)

    @property
    def value(self) -> float:
        value = {
            RfModulationKind.AM: self.depth_percent,
            RfModulationKind.FM: self.frequency_deviation_hz,
            RfModulationKind.PM: self.phase_deviation_rad,
        }[self.kind]
        assert value is not None
        return value

    @property
    def value_unit(self) -> RfModulationValueUnit:
        return {
            RfModulationKind.AM: RfModulationValueUnit.PERCENT,
            RfModulationKind.FM: RfModulationValueUnit.HZ,
            RfModulationKind.PM: RfModulationValueUnit.RAD,
        }[self.kind]


@dataclass(frozen=True, slots=True)
class RfPulseConfigureRequest:
    """One RF-OFF internal single-pulse configuration for one RF port.

    The descriptor supplies the fixed source and mode; this request only
    carries bounded timing and polarity fields. It deliberately has no trigger,
    external-port, or pulse-output field.
    """

    port_id: str
    period_s: float
    width_s: float
    polarity: RfPulsePolarity

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF pulse configure request port_id")
        _require_finite(self.period_s, "RF pulse configure request period_s", minimum=0.0)
        _require_finite(self.width_s, "RF pulse configure request width_s", minimum=0.0)
        if self.period_s <= 0.0:
            raise ValueError("RF pulse configure request period_s must be positive")
        if self.width_s <= 0.0:
            raise ValueError("RF pulse configure request width_s must be positive")
        if self.width_s >= self.period_s:
            raise ValueError("RF pulse configure request width_s must be less than period_s")
        if not isinstance(self.polarity, RfPulsePolarity):
            raise ValueError("RF pulse configure request polarity has an invalid type")


@dataclass(frozen=True, slots=True)
class RfPulseConfigureResult:
    """A bounded RF pulse configuration confirmed by independent readback."""

    port_id: str
    period_s: float
    width_s: float
    polarity: RfPulsePolarity

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF pulse configure result port_id")
        _require_finite(self.period_s, "RF pulse configure result period_s", minimum=0.0)
        _require_finite(self.width_s, "RF pulse configure result width_s", minimum=0.0)
        if self.period_s <= 0.0:
            raise ValueError("RF pulse configure result period_s must be positive")
        if self.width_s <= 0.0:
            raise ValueError("RF pulse configure result width_s must be positive")
        if self.width_s >= self.period_s:
            raise ValueError("RF pulse configure result width_s must be less than period_s")
        if not isinstance(self.polarity, RfPulsePolarity):
            raise ValueError("RF pulse configure result polarity has an invalid type")


@dataclass(frozen=True, slots=True)
class RfPulseOutputRequest:
    """Set one declared physical Pulse-output interface on or off.

    Timing, polarity, electrical levels, and physical direction are all fixed
    by the descriptor.  This request cannot select an input direction, a
    trigger source, a receiving instrument, or arbitrary output levels.
    """

    port_id: str
    interface_id: str
    enabled: bool

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF pulse-output request port_id")
        _require_token(self.interface_id, "RF pulse-output request interface_id")
        _require_bool(self.enabled, "RF pulse-output request enabled")


@dataclass(frozen=True, slots=True)
class RfPulseOutputResult:
    """A physical Pulse-output target confirmed by independent readback."""

    port_id: str
    interface_id: str
    enabled: bool
    write_completed: bool

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF pulse-output result port_id")
        _require_token(self.interface_id, "RF pulse-output result interface_id")
        _require_bool(self.enabled, "RF pulse-output result enabled")
        _require_bool(self.write_completed, "RF pulse-output result write_completed")


@dataclass(frozen=True, slots=True)
class RfPulseSnapshot:
    """Complete typed readback for one pulse profile on one RF port."""

    port_id: str
    source: RfPulseSource
    mode: RfPulseMode
    period_s: float
    width_s: float
    polarity: RfPulsePolarity
    state: RfPulseState

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF pulse snapshot port_id")
        if not isinstance(self.source, RfPulseSource):
            raise ValueError("RF pulse snapshot source has an invalid type")
        if not isinstance(self.mode, RfPulseMode):
            raise ValueError("RF pulse snapshot mode has an invalid type")
        _require_finite(self.period_s, "RF pulse snapshot period_s", minimum=0.0)
        _require_finite(self.width_s, "RF pulse snapshot width_s", minimum=0.0)
        if self.period_s <= 0.0:
            raise ValueError("RF pulse snapshot period_s must be positive")
        if self.width_s <= 0.0:
            raise ValueError("RF pulse snapshot width_s must be positive")
        if self.width_s >= self.period_s:
            raise ValueError("RF pulse snapshot width_s must be less than period_s")
        if not isinstance(self.polarity, RfPulsePolarity):
            raise ValueError("RF pulse snapshot polarity has an invalid type")
        if not isinstance(self.state, RfPulseState):
            raise ValueError("RF pulse snapshot state has an invalid type")


@dataclass(frozen=True, slots=True)
class RfPulseOutputSnapshot:
    """Typed readback of one declared physical Pulse-output interface."""

    port_id: str
    interface_id: str
    direction: RfPulseOutputDirection
    enabled: bool
    low_level_v: float
    high_level_v: float
    output_impedance_ohm: float
    source: RfPulseSource
    mode: RfPulseMode
    period_s: float
    width_s: float
    polarity: RfPulsePolarity
    pulse_state: RfPulseState

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF pulse-output snapshot port_id")
        _require_token(self.interface_id, "RF pulse-output snapshot interface_id")
        if self.direction is not RfPulseOutputDirection.OUTPUT:
            raise ValueError("RF pulse-output snapshots must report output direction")
        _require_bool(self.enabled, "RF pulse-output snapshot enabled")
        _require_finite(self.low_level_v, "RF pulse-output snapshot low_level_v", minimum=0.0)
        _require_finite(
            self.high_level_v,
            "RF pulse-output snapshot high_level_v",
            minimum=self.low_level_v,
        )
        if self.high_level_v <= self.low_level_v:
            raise ValueError("RF pulse-output snapshot high_level_v must exceed low_level_v")
        _require_finite(
            self.output_impedance_ohm,
            "RF pulse-output snapshot output_impedance_ohm",
            minimum=0.0,
        )
        if self.output_impedance_ohm <= 0.0:
            raise ValueError("RF pulse-output snapshot output_impedance_ohm must be positive")
        if not isinstance(self.source, RfPulseSource):
            raise ValueError("RF pulse-output snapshot source has an invalid type")
        if not isinstance(self.mode, RfPulseMode):
            raise ValueError("RF pulse-output snapshot mode has an invalid type")
        _require_finite(self.period_s, "RF pulse-output snapshot period_s", minimum=0.0)
        _require_finite(self.width_s, "RF pulse-output snapshot width_s", minimum=0.0)
        if self.period_s <= 0.0 or self.width_s <= 0.0:
            raise ValueError("RF pulse-output snapshot period_s and width_s must be positive")
        if self.width_s >= self.period_s:
            raise ValueError("RF pulse-output snapshot width_s must be less than period_s")
        if not isinstance(self.polarity, RfPulsePolarity):
            raise ValueError("RF pulse-output snapshot polarity has an invalid type")
        if not isinstance(self.pulse_state, RfPulseState):
            raise ValueError("RF pulse-output snapshot pulse_state has an invalid type")

    def as_dict(self) -> dict[str, object]:
        return rf_pulse_output_snapshot_document(self)


@dataclass(frozen=True, slots=True)
class RfSweepConfigureRequest:
    """One RF-OFF frequency-only Step Sweep configuration for one RF port.

    The descriptor supplies the fixed Step／forward／ramp／linear profile. This
    request deliberately has no level, trigger, arm, fire, or output field.
    """

    port_id: str
    start_frequency_hz: float
    stop_frequency_hz: float
    points: int
    dwell_s: float

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF sweep configure request port_id")
        _require_finite(
            self.start_frequency_hz,
            "RF sweep configure request start_frequency_hz",
            minimum=0.0,
        )
        _require_finite(
            self.stop_frequency_hz,
            "RF sweep configure request stop_frequency_hz",
            minimum=0.0,
        )
        if self.start_frequency_hz >= self.stop_frequency_hz:
            raise ValueError("RF sweep configure request start_frequency_hz must be less than stop_frequency_hz")
        _require_integer(self.points, "RF sweep configure request points", minimum=2)
        _require_finite(self.dwell_s, "RF sweep configure request dwell_s", minimum=0.0)
        if self.dwell_s <= 0.0:
            raise ValueError("RF sweep configure request dwell_s must be positive")


@dataclass(frozen=True, slots=True)
class RfSweepConfigureResult:
    """A bounded Step Sweep configuration confirmed while Sweep remains disabled."""

    port_id: str
    start_frequency_hz: float
    stop_frequency_hz: float
    points: int
    dwell_s: float

    def __post_init__(self) -> None:
        RfSweepConfigureRequest(
            port_id=self.port_id,
            start_frequency_hz=self.start_frequency_hz,
            stop_frequency_hz=self.stop_frequency_hz,
            points=self.points,
            dwell_s=self.dwell_s,
        )


@dataclass(frozen=True, slots=True)
class RfSweepSnapshot:
    """Complete typed readback for one frequency-only Step Sweep profile."""

    port_id: str
    sweep_type: RfSweepType
    direction: RfSweepDirection
    shape: RfSweepShape
    spacing: RfSweepSpacing
    start_frequency_hz: float
    stop_frequency_hz: float
    points: int
    dwell_s: float
    state: RfSweepState

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF sweep snapshot port_id")
        if not isinstance(self.sweep_type, RfSweepType):
            raise ValueError("RF sweep snapshot sweep_type has an invalid type")
        if not isinstance(self.direction, RfSweepDirection):
            raise ValueError("RF sweep snapshot direction has an invalid type")
        if not isinstance(self.shape, RfSweepShape):
            raise ValueError("RF sweep snapshot shape has an invalid type")
        if not isinstance(self.spacing, RfSweepSpacing):
            raise ValueError("RF sweep snapshot spacing has an invalid type")
        _require_finite(
            self.start_frequency_hz,
            "RF sweep snapshot start_frequency_hz",
            minimum=0.0,
        )
        _require_finite(
            self.stop_frequency_hz,
            "RF sweep snapshot stop_frequency_hz",
            minimum=0.0,
        )
        if self.start_frequency_hz >= self.stop_frequency_hz:
            raise ValueError("RF sweep snapshot start_frequency_hz must be less than stop_frequency_hz")
        _require_integer(self.points, "RF sweep snapshot points", minimum=2)
        _require_finite(self.dwell_s, "RF sweep snapshot dwell_s", minimum=0.0)
        if self.dwell_s <= 0.0:
            raise ValueError("RF sweep snapshot dwell_s must be positive")
        if not isinstance(self.state, RfSweepState):
            raise ValueError("RF sweep snapshot state has an invalid type")


@dataclass(frozen=True, slots=True)
class RfTriggerSnapshot:
    """Complete readback of logical Pulse and Sweep trigger configuration.

    ``port_id`` identifies the RF output whose behavior the queried settings
    govern.  It is not a physical trigger/sync connector identifier.
    """

    port_id: str
    pulse_trigger_mode: RfPulseTriggerMode
    pulse_external_trigger_edge: RfExternalTriggerEdge
    pulse_external_gate_polarity: RfExternalGatePolarity
    sweep_mode: RfSweepMode
    sweep_period_trigger_mode: RfSweepTriggerMode
    sweep_point_trigger_mode: RfSweepTriggerMode

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF trigger snapshot port_id")
        if not isinstance(self.pulse_trigger_mode, RfPulseTriggerMode):
            raise ValueError("RF trigger snapshot pulse_trigger_mode has an invalid type")
        if not isinstance(self.pulse_external_trigger_edge, RfExternalTriggerEdge):
            raise ValueError(
                "RF trigger snapshot pulse_external_trigger_edge has an invalid type"
            )
        if not isinstance(self.pulse_external_gate_polarity, RfExternalGatePolarity):
            raise ValueError(
                "RF trigger snapshot pulse_external_gate_polarity has an invalid type"
            )
        if not isinstance(self.sweep_mode, RfSweepMode):
            raise ValueError("RF trigger snapshot sweep_mode has an invalid type")
        if not isinstance(self.sweep_period_trigger_mode, RfSweepTriggerMode):
            raise ValueError("RF trigger snapshot sweep_period_trigger_mode has an invalid type")
        if not isinstance(self.sweep_point_trigger_mode, RfSweepTriggerMode):
            raise ValueError("RF trigger snapshot sweep_point_trigger_mode has an invalid type")

    def as_dict(self) -> dict[str, object]:
        return rf_trigger_snapshot_document(self)


@dataclass(frozen=True, slots=True)
class RfOutputRequest:
    """One explicit RF output state request for one descriptor-defined port."""

    port_id: str
    enabled: bool

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF output request port_id")
        _require_bool(self.enabled, "RF output request enabled")


@dataclass(frozen=True, slots=True)
class RfOutputResult:
    """An RF output target confirmed by a fresh postcondition snapshot."""

    port_id: str
    enabled: bool
    write_completed: bool

    def __post_init__(self) -> None:
        _require_token(self.port_id, "RF output result port_id")
        _require_bool(self.enabled, "RF output result enabled")
        _require_bool(self.write_completed, "RF output result write_completed")


@runtime_checkable
class RfSourceDriver(InstrumentDriver, Protocol):
    def get_rf_snapshot(self) -> RfSourceSnapshot: ...

    def get_rf_trigger_snapshot(self, port_id: str) -> RfTriggerSnapshot: ...

    def configure_cw(self, request: RfCwRequest) -> None: ...

    def get_rf_modulation_state(self, port_id: str) -> RfModulationStateSnapshot: ...

    def get_rf_modulation_snapshot(
        self,
        port_id: str,
        kind: RfModulationKind,
    ) -> RfModulationSnapshot: ...

    def configure_rf_modulation(self, request: RfModulationRequest) -> None: ...

    def disable_rf_modulation(self, request: RfModulationDisableRequest) -> None: ...

    def get_rf_pulse_snapshot(self, port_id: str) -> RfPulseSnapshot: ...

    def configure_rf_pulse(self, request: RfPulseConfigureRequest) -> None: ...

    def get_rf_pulse_output_snapshot(
        self,
        port_id: str,
        interface_id: str,
    ) -> RfPulseOutputSnapshot: ...

    def set_rf_pulse_output(self, request: RfPulseOutputRequest) -> None: ...

    def get_rf_sweep_snapshot(self, port_id: str) -> RfSweepSnapshot: ...

    def configure_rf_sweep(self, request: RfSweepConfigureRequest) -> None: ...

    def set_rf_output(self, request: RfOutputRequest) -> None: ...


def _require_observed_number(
    observed: object,
    label: str,
    *,
    minimum: float | None = None,
) -> None:
    if not isinstance(observed, RfObserved):
        raise ValueError(f"{label} has an invalid type")
    if observed.availability is RfAvailability.VALUE:
        _require_finite(observed.value, label, minimum=minimum)


def _require_observed_value(
    observed: object,
    expected_type: type[object],
    label: str,
) -> None:
    if not isinstance(observed, RfObserved):
        raise ValueError(f"{label} has an invalid type")
    if observed.availability is RfAvailability.VALUE and not isinstance(
        observed.value, expected_type
    ):
        raise ValueError(f"{label} has an invalid VALUE type")


def rf_source_to_data(value: object) -> object:
    """Convert public RF-source values into strict JSON-compatible data."""

    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("RF source JSON cannot contain non-finite floats")
        return value
    if isinstance(value, tuple):
        return [rf_source_to_data(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        payload: dict[str, object] = {"type": type(value).__name__}
        for item in fields(value):
            payload[item.name] = rf_source_to_data(getattr(value, item.name))
        return payload
    raise TypeError(f"unsupported RF source JSON value: {type(value).__name__}")


def rf_source_canonical_json(value: object) -> str:
    return json.dumps(
        rf_source_to_data(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def rf_source_digest(value: object) -> str:
    return "sha256:" + sha256(rf_source_canonical_json(value).encode("utf-8")).hexdigest()


def rf_source_snapshot_document(snapshot: RfSourceSnapshot) -> dict[str, object]:
    if not isinstance(snapshot, RfSourceSnapshot):
        raise TypeError("snapshot must be RfSourceSnapshot")
    data = rf_source_to_data(snapshot)
    assert isinstance(data, dict)
    return {"schema": RF_SOURCE_SNAPSHOT_SCHEMA, **data}


def rf_modulation_snapshot_document(snapshot: RfModulationSnapshot) -> dict[str, object]:
    """Build a redacted document for one typed RF modulation readback."""

    if not isinstance(snapshot, RfModulationSnapshot):
        raise TypeError("snapshot must be RfModulationSnapshot")
    data = rf_source_to_data(snapshot)
    assert isinstance(data, dict)
    return {"schema": RF_SOURCE_MODULATION_SNAPSHOT_SCHEMA, **data}


def rf_modulation_state_snapshot_document(
    snapshot: RfModulationStateSnapshot,
) -> dict[str, object]:
    """Build a redacted document for one typed RF modulation-state readback."""

    if not isinstance(snapshot, RfModulationStateSnapshot):
        raise TypeError("snapshot must be RfModulationStateSnapshot")
    data = rf_source_to_data(snapshot)
    assert isinstance(data, dict)
    return {"schema": RF_SOURCE_MODULATION_STATE_SCHEMA, **data}


def rf_pulse_snapshot_document(snapshot: RfPulseSnapshot) -> dict[str, object]:
    """Build a redacted document for one typed RF pulse readback."""

    if not isinstance(snapshot, RfPulseSnapshot):
        raise TypeError("snapshot must be RfPulseSnapshot")
    data = rf_source_to_data(snapshot)
    assert isinstance(data, dict)
    return {"schema": RF_SOURCE_PULSE_SNAPSHOT_SCHEMA, **data}


def rf_pulse_output_snapshot_document(snapshot: RfPulseOutputSnapshot) -> dict[str, object]:
    """Build a redacted document for one physical RF Pulse-output readback."""

    if not isinstance(snapshot, RfPulseOutputSnapshot):
        raise TypeError("snapshot must be RfPulseOutputSnapshot")
    data = rf_source_to_data(snapshot)
    assert isinstance(data, dict)
    return {"schema": RF_SOURCE_PULSE_OUTPUT_SNAPSHOT_SCHEMA, **data}


def rf_sweep_snapshot_document(snapshot: RfSweepSnapshot) -> dict[str, object]:
    """Build a redacted document for one typed RF Step Sweep readback."""

    if not isinstance(snapshot, RfSweepSnapshot):
        raise TypeError("snapshot must be RfSweepSnapshot")
    data = rf_source_to_data(snapshot)
    assert isinstance(data, dict)
    return {"schema": RF_SOURCE_SWEEP_SNAPSHOT_SCHEMA, **data}


def rf_trigger_snapshot_document(snapshot: RfTriggerSnapshot) -> dict[str, object]:
    """Build a redacted document for one typed RF trigger configuration readback."""

    if not isinstance(snapshot, RfTriggerSnapshot):
        raise TypeError("snapshot must be RfTriggerSnapshot")
    data = rf_source_to_data(snapshot)
    assert isinstance(data, dict)
    return {"schema": RF_SOURCE_TRIGGER_SNAPSHOT_SCHEMA, **data}


def rf_source_snapshot_operation_artifact(snapshot: RfSourceSnapshot) -> dict[str, object]:
    """Build a read-only snapshot artifact without transport-private values."""

    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "rf_source.snapshot",
        "snapshot": rf_source_snapshot_document(snapshot),
    }


def rf_source_trigger_snapshot_operation_artifact(
    snapshot: RfTriggerSnapshot,
) -> dict[str, object]:
    """Build a read-only trigger-configuration artifact without transport values."""

    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "rf_source.trigger_snapshot",
        "trigger_snapshot": rf_trigger_snapshot_document(snapshot),
    }


def rf_source_cw_operation_artifact(
    request: RfCwRequest,
    result: RfCwResult,
    *,
    preflight_snapshot: RfSourceSnapshot,
    postcondition_snapshot: RfSourceSnapshot,
) -> dict[str, object]:
    """Build one redacted M1 CW operation artifact from typed evidence."""

    if not isinstance(request, RfCwRequest):
        raise TypeError("request must be RfCwRequest")
    if not isinstance(result, RfCwResult):
        raise TypeError("result must be RfCwResult")
    if not isinstance(preflight_snapshot, RfSourceSnapshot):
        raise TypeError("preflight_snapshot must be RfSourceSnapshot")
    if not isinstance(postcondition_snapshot, RfSourceSnapshot):
        raise TypeError("postcondition_snapshot must be RfSourceSnapshot")
    operation = (
        "rf_source.set_frequency"
        if request.frequency_hz is not None
        else "rf_source.set_power_dbm"
    )
    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": operation,
        "request": rf_source_to_data(request),
        "result": rf_source_to_data(result),
        "preflight_snapshot": rf_source_snapshot_document(preflight_snapshot),
        "postcondition_snapshot": rf_source_snapshot_document(postcondition_snapshot),
    }


def rf_source_modulation_operation_artifact(
    request: RfModulationRequest,
    result: RfModulationResult,
    *,
    preflight_snapshot: RfSourceSnapshot,
    preflight_modulation_state: RfModulationStateSnapshot,
    postcondition_snapshot: RfSourceSnapshot,
    postcondition_modulation_snapshot: RfModulationSnapshot,
) -> dict[str, object]:
    """Build one redacted M3 modulation operation artifact from typed evidence."""

    if not isinstance(request, RfModulationRequest):
        raise TypeError("request must be RfModulationRequest")
    if not isinstance(result, RfModulationResult):
        raise TypeError("result must be RfModulationResult")
    if (
        request.port_id != result.port_id
        or request.kind is not result.kind
        or request.internal_frequency_hz != result.internal_frequency_hz
        or request.value != result.value
        or request.value_unit is not result.value_unit
    ):
        raise ValueError("RF modulation request and result must describe the same target")
    if not isinstance(preflight_snapshot, RfSourceSnapshot):
        raise TypeError("preflight_snapshot must be RfSourceSnapshot")
    if not isinstance(preflight_modulation_state, RfModulationStateSnapshot):
        raise TypeError("preflight_modulation_state must be RfModulationStateSnapshot")
    if not isinstance(postcondition_snapshot, RfSourceSnapshot):
        raise TypeError("postcondition_snapshot must be RfSourceSnapshot")
    if not isinstance(postcondition_modulation_snapshot, RfModulationSnapshot):
        raise TypeError("postcondition_modulation_snapshot must be RfModulationSnapshot")
    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "rf_source.modulation_configure",
        "request": rf_source_to_data(request),
        "result": rf_source_to_data(result),
        "preflight_snapshot": rf_source_snapshot_document(preflight_snapshot),
        "preflight_modulation_state": rf_modulation_state_snapshot_document(
            preflight_modulation_state
        ),
        "postcondition_snapshot": rf_source_snapshot_document(postcondition_snapshot),
        "postcondition_modulation_snapshot": rf_modulation_snapshot_document(
            postcondition_modulation_snapshot
        ),
    }


def rf_source_modulated_output_operation_artifact(
    request: RfModulatedOutputRequest,
    result: RfModulatedOutputResult,
    *,
    preflight_snapshot: RfSourceSnapshot,
    preflight_modulation_snapshot: RfModulationSnapshot,
    postcondition_snapshot: RfSourceSnapshot,
    postcondition_modulation_snapshot: RfModulationSnapshot,
) -> dict[str, object]:
    """Build typed evidence for one separate modulated RF-output enable.

    The artifact records the declared active profile both before and after the
    one RF-ON write. It intentionally does not claim that RF OFF or modulation
    disable was restored; those are separate operations and evidence.
    """

    if not isinstance(request, RfModulatedOutputRequest):
        raise TypeError("request must be RfModulatedOutputRequest")
    if not isinstance(result, RfModulatedOutputResult):
        raise TypeError("result must be RfModulatedOutputResult")
    modulation_request = request.modulation
    modulation_result = result.modulation
    if (
        modulation_request.port_id != modulation_result.port_id
        or modulation_request.kind is not modulation_result.kind
        or modulation_request.internal_frequency_hz != modulation_result.internal_frequency_hz
        or modulation_request.value != modulation_result.value
        or modulation_request.value_unit is not modulation_result.value_unit
    ):
        raise ValueError("RF modulated-output request and result must describe the same target")
    if not isinstance(preflight_snapshot, RfSourceSnapshot):
        raise TypeError("preflight_snapshot must be RfSourceSnapshot")
    if not isinstance(preflight_modulation_snapshot, RfModulationSnapshot):
        raise TypeError("preflight_modulation_snapshot must be RfModulationSnapshot")
    if not isinstance(postcondition_snapshot, RfSourceSnapshot):
        raise TypeError("postcondition_snapshot must be RfSourceSnapshot")
    if not isinstance(postcondition_modulation_snapshot, RfModulationSnapshot):
        raise TypeError("postcondition_modulation_snapshot must be RfModulationSnapshot")
    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "rf_source.modulated_output_enable",
        "request": rf_source_to_data(request),
        "result": rf_source_to_data(result),
        "preflight_snapshot": rf_source_snapshot_document(preflight_snapshot),
        "preflight_modulation_snapshot": rf_modulation_snapshot_document(
            preflight_modulation_snapshot
        ),
        "postcondition_snapshot": rf_source_snapshot_document(postcondition_snapshot),
        "postcondition_modulation_snapshot": rf_modulation_snapshot_document(
            postcondition_modulation_snapshot
        ),
    }


def rf_source_modulation_disable_operation_artifact(
    request: RfModulationDisableRequest,
    result: RfModulationDisableResult,
    *,
    preflight_snapshot: RfSourceSnapshot,
    preflight_modulation_state: RfModulationStateSnapshot,
    postcondition_snapshot: RfSourceSnapshot,
    postcondition_modulation_state: RfModulationStateSnapshot,
) -> dict[str, object]:
    """Build redacted typed evidence for one RF modulation-disable operation."""

    if not isinstance(request, RfModulationDisableRequest):
        raise TypeError("request must be RfModulationDisableRequest")
    if not isinstance(result, RfModulationDisableResult):
        raise TypeError("result must be RfModulationDisableResult")
    if request.port_id != result.port_id or request.kind is not result.kind:
        raise ValueError("RF modulation disable request and result must describe the same target")
    if not isinstance(preflight_snapshot, RfSourceSnapshot):
        raise TypeError("preflight_snapshot must be RfSourceSnapshot")
    if not isinstance(preflight_modulation_state, RfModulationStateSnapshot):
        raise TypeError("preflight_modulation_state must be RfModulationStateSnapshot")
    if not isinstance(postcondition_snapshot, RfSourceSnapshot):
        raise TypeError("postcondition_snapshot must be RfSourceSnapshot")
    if not isinstance(postcondition_modulation_state, RfModulationStateSnapshot):
        raise TypeError("postcondition_modulation_state must be RfModulationStateSnapshot")
    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "rf_source.modulation_disable",
        "request": rf_source_to_data(request),
        "result": rf_source_to_data(result),
        "preflight_snapshot": rf_source_snapshot_document(preflight_snapshot),
        "preflight_modulation_state": rf_modulation_state_snapshot_document(
            preflight_modulation_state
        ),
        "postcondition_snapshot": rf_source_snapshot_document(postcondition_snapshot),
        "postcondition_modulation_state": rf_modulation_state_snapshot_document(
            postcondition_modulation_state
        ),
    }


def rf_source_pulse_operation_artifact(
    request: RfPulseConfigureRequest,
    result: RfPulseConfigureResult,
    *,
    preflight_snapshot: RfSourceSnapshot,
    postcondition_snapshot: RfSourceSnapshot,
    postcondition_pulse_snapshot: RfPulseSnapshot,
) -> dict[str, object]:
    """Build redacted typed evidence for one RF-OFF pulse configuration."""

    if not isinstance(request, RfPulseConfigureRequest):
        raise TypeError("request must be RfPulseConfigureRequest")
    if not isinstance(result, RfPulseConfigureResult):
        raise TypeError("result must be RfPulseConfigureResult")
    if (
        request.port_id != result.port_id
        or request.period_s != result.period_s
        or request.width_s != result.width_s
        or request.polarity is not result.polarity
    ):
        raise ValueError("RF pulse request and result must describe the same target")
    if not isinstance(preflight_snapshot, RfSourceSnapshot):
        raise TypeError("preflight_snapshot must be RfSourceSnapshot")
    if not isinstance(postcondition_snapshot, RfSourceSnapshot):
        raise TypeError("postcondition_snapshot must be RfSourceSnapshot")
    if not isinstance(postcondition_pulse_snapshot, RfPulseSnapshot):
        raise TypeError("postcondition_pulse_snapshot must be RfPulseSnapshot")
    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "rf_source.pulse_configure",
        "request": rf_source_to_data(request),
        "result": rf_source_to_data(result),
        "preflight_snapshot": rf_source_snapshot_document(preflight_snapshot),
        "postcondition_snapshot": rf_source_snapshot_document(postcondition_snapshot),
        "postcondition_pulse_snapshot": rf_pulse_snapshot_document(postcondition_pulse_snapshot),
    }


def rf_source_pulse_output_operation_artifact(
    request: RfPulseOutputRequest,
    result: RfPulseOutputResult,
    *,
    preflight_snapshot: RfSourceSnapshot,
    preflight_pulse_output_snapshot: RfPulseOutputSnapshot,
    postcondition_snapshot: RfSourceSnapshot,
    postcondition_pulse_output_snapshot: RfPulseOutputSnapshot,
) -> dict[str, object]:
    """Build redacted typed evidence for one physical Pulse-output state change."""

    if not isinstance(request, RfPulseOutputRequest):
        raise TypeError("request must be RfPulseOutputRequest")
    if not isinstance(result, RfPulseOutputResult):
        raise TypeError("result must be RfPulseOutputResult")
    if (
        request.port_id != result.port_id
        or request.interface_id != result.interface_id
        or request.enabled is not result.enabled
    ):
        raise ValueError("RF pulse-output request and result must describe the same target")
    if not isinstance(preflight_snapshot, RfSourceSnapshot):
        raise TypeError("preflight_snapshot must be RfSourceSnapshot")
    if not isinstance(preflight_pulse_output_snapshot, RfPulseOutputSnapshot):
        raise TypeError("preflight_pulse_output_snapshot must be RfPulseOutputSnapshot")
    if not isinstance(postcondition_snapshot, RfSourceSnapshot):
        raise TypeError("postcondition_snapshot must be RfSourceSnapshot")
    if not isinstance(postcondition_pulse_output_snapshot, RfPulseOutputSnapshot):
        raise TypeError("postcondition_pulse_output_snapshot must be RfPulseOutputSnapshot")
    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": (
            "rf_source.pulse_output_enable"
            if request.enabled
            else "rf_source.pulse_output_disable"
        ),
        "request": rf_source_to_data(request),
        "result": rf_source_to_data(result),
        "preflight_snapshot": rf_source_snapshot_document(preflight_snapshot),
        "preflight_pulse_output_snapshot": rf_pulse_output_snapshot_document(
            preflight_pulse_output_snapshot
        ),
        "postcondition_snapshot": rf_source_snapshot_document(postcondition_snapshot),
        "postcondition_pulse_output_snapshot": rf_pulse_output_snapshot_document(
            postcondition_pulse_output_snapshot
        ),
    }


def rf_source_sweep_operation_artifact(
    request: RfSweepConfigureRequest,
    result: RfSweepConfigureResult,
    *,
    preflight_snapshot: RfSourceSnapshot,
    postcondition_snapshot: RfSourceSnapshot,
    postcondition_sweep_snapshot: RfSweepSnapshot,
) -> dict[str, object]:
    """Build redacted typed evidence for one disabled Step Sweep configuration."""

    if not isinstance(request, RfSweepConfigureRequest):
        raise TypeError("request must be RfSweepConfigureRequest")
    if not isinstance(result, RfSweepConfigureResult):
        raise TypeError("result must be RfSweepConfigureResult")
    if (
        request.port_id != result.port_id
        or request.start_frequency_hz != result.start_frequency_hz
        or request.stop_frequency_hz != result.stop_frequency_hz
        or request.points != result.points
        or request.dwell_s != result.dwell_s
    ):
        raise ValueError("RF sweep request and result must describe the same target")
    if not isinstance(preflight_snapshot, RfSourceSnapshot):
        raise TypeError("preflight_snapshot must be RfSourceSnapshot")
    if not isinstance(postcondition_snapshot, RfSourceSnapshot):
        raise TypeError("postcondition_snapshot must be RfSourceSnapshot")
    if not isinstance(postcondition_sweep_snapshot, RfSweepSnapshot):
        raise TypeError("postcondition_sweep_snapshot must be RfSweepSnapshot")
    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "rf_source.sweep_configure",
        "request": rf_source_to_data(request),
        "result": rf_source_to_data(result),
        "preflight_snapshot": rf_source_snapshot_document(preflight_snapshot),
        "postcondition_snapshot": rf_source_snapshot_document(postcondition_snapshot),
        "postcondition_sweep_snapshot": rf_sweep_snapshot_document(postcondition_sweep_snapshot),
    }


def rf_source_output_operation_artifact(
    request: RfOutputRequest,
    result: RfOutputResult,
    *,
    preflight_snapshot: RfSourceSnapshot,
    postcondition_snapshot: RfSourceSnapshot,
) -> dict[str, object]:
    """Build one redacted M2 RF-output artifact from typed evidence."""

    if not isinstance(request, RfOutputRequest):
        raise TypeError("request must be RfOutputRequest")
    if not isinstance(result, RfOutputResult):
        raise TypeError("result must be RfOutputResult")
    if request.port_id != result.port_id or request.enabled is not result.enabled:
        raise ValueError("RF output request and result must describe the same target")
    if not isinstance(preflight_snapshot, RfSourceSnapshot):
        raise TypeError("preflight_snapshot must be RfSourceSnapshot")
    if not isinstance(postcondition_snapshot, RfSourceSnapshot):
        raise TypeError("postcondition_snapshot must be RfSourceSnapshot")
    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": (
            "rf_source.output_enable" if request.enabled else "rf_source.output_disable"
        ),
        "request": rf_source_to_data(request),
        "result": rf_source_to_data(result),
        "preflight_snapshot": rf_source_snapshot_document(preflight_snapshot),
        "postcondition_snapshot": rf_source_snapshot_document(postcondition_snapshot),
    }


__all__ = [
    "RF_SOURCE_CONTRACT_VERSION",
    "RF_SOURCE_MODULATION_STATE_SCHEMA",
    "RF_SOURCE_MODULATION_SNAPSHOT_SCHEMA",
    "RF_SOURCE_OPERATION_ARTIFACT_SCHEMA",
    "RF_SOURCE_PULSE_SNAPSHOT_SCHEMA",
    "RF_SOURCE_PULSE_OUTPUT_SNAPSHOT_SCHEMA",
    "RF_SOURCE_SWEEP_SNAPSHOT_SCHEMA",
    "RF_SOURCE_SNAPSHOT_MIN_CORE_VERSION",
    "RF_SOURCE_SNAPSHOT_SCHEMA",
    "RF_SOURCE_TRIGGER_SNAPSHOT_SCHEMA",
    "RfAvailability",
    "RfCwProfile",
    "RfCwRequest",
    "RfCwResult",
    "RfExternalGatePolarity",
    "RfExternalTriggerEdge",
    "RfFeature",
    "RfFeatureCapability",
    "RfFeatureDirection",
    "RfFeatureProfile",
    "RfModulationKind",
    "RfModulationDisableRequest",
    "RfModulationDisableResult",
    "RfModulationModeProfile",
    "RfModulatedOutputProfile",
    "RfModulatedOutputRequest",
    "RfModulatedOutputResult",
    "RfModulationProfile",
    "RfModulationRequest",
    "RfModulationResult",
    "RfModulationStateSnapshot",
    "RfModulationSnapshot",
    "RfModulationSource",
    "RfModulationState",
    "RfModulationValueUnit",
    "RfModulationWaveform",
    "RfObserved",
    "RfOutputPortProfile",
    "RfOutputProfile",
    "RfOutputRequest",
    "RfOutputResult",
    "RfPortSnapshot",
    "RfProtectionConditionPolicy",
    "RfProtectionStatus",
    "RfPulseProfile",
    "RfPulseConfigureRequest",
    "RfPulseConfigureResult",
    "RfPulseMode",
    "RfPulseModeProfile",
    "RfPulseOutputDirection",
    "RfPulseOutputProfile",
    "RfPulseOutputRequest",
    "RfPulseOutputResult",
    "RfPulseOutputSnapshot",
    "RfPulsePolarity",
    "RfPulseSnapshot",
    "RfPulseSource",
    "RfPulseState",
    "RfPulseTriggerMode",
    "RfReasonCode",
    "RfSourceDescriptorExtensions",
    "RfSourceDriver",
    "RfSourceSnapshot",
    "RfSourceTopology",
    "RfSweepProfile",
    "RfSweepConfigureRequest",
    "RfSweepConfigureResult",
    "RfSweepDirection",
    "RfSweepModeProfile",
    "RfSweepShape",
    "RfSweepSnapshot",
    "RfSweepSpacing",
    "RfSweepState",
    "RfSweepMode",
    "RfSweepTriggerMode",
    "RfSweepType",
    "RfTriggerProfile",
    "RfTriggerSnapshot",
    "rf_source_canonical_json",
    "rf_source_cw_operation_artifact",
    "rf_source_digest",
    "rf_modulation_snapshot_document",
    "rf_modulation_state_snapshot_document",
    "rf_pulse_snapshot_document",
    "rf_pulse_output_snapshot_document",
    "rf_sweep_snapshot_document",
    "rf_trigger_snapshot_document",
    "rf_source_modulation_disable_operation_artifact",
    "rf_source_modulated_output_operation_artifact",
    "rf_source_modulation_operation_artifact",
    "rf_source_pulse_operation_artifact",
    "rf_source_pulse_output_operation_artifact",
    "rf_source_sweep_operation_artifact",
    "rf_source_snapshot_document",
    "rf_source_snapshot_operation_artifact",
    "rf_source_trigger_snapshot_operation_artifact",
    "rf_source_output_operation_artifact",
    "rf_source_to_data",
]
