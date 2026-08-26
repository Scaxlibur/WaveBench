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


class RfPulseState(StrEnum):
    DISABLED = "disabled"
    ENABLED = "enabled"


class RfSweepState(StrEnum):
    DISABLED = "disabled"
    ENABLED = "enabled"


class RfFeature(StrEnum):
    CW = "cw"
    MODULATION = "modulation"
    OUTPUT = "output"
    PULSE = "pulse"
    SWEEP = "sweep"


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

    def __post_init__(self) -> None:
        _require_bool(self.state_readable, "RF modulation state_readable")


@dataclass(frozen=True, slots=True)
class RfPulseProfile:
    state_readable: bool

    def __post_init__(self) -> None:
        _require_bool(self.state_readable, "RF pulse state_readable")


@dataclass(frozen=True, slots=True)
class RfSweepProfile:
    state_readable: bool

    def __post_init__(self) -> None:
        _require_bool(self.state_readable, "RF sweep state_readable")


RfFeatureProfile: TypeAlias = (
    RfCwProfile | RfOutputProfile | RfModulationProfile | RfPulseProfile | RfSweepProfile
)

_FEATURE_PROFILE_TYPES: dict[RfFeature, type[RfFeatureProfile]] = {
    RfFeature.CW: RfCwProfile,
    RfFeature.MODULATION: RfModulationProfile,
    RfFeature.OUTPUT: RfOutputProfile,
    RfFeature.PULSE: RfPulseProfile,
    RfFeature.SWEEP: RfSweepProfile,
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

    def configure_cw(self, request: RfCwRequest) -> None: ...

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


def rf_source_snapshot_operation_artifact(snapshot: RfSourceSnapshot) -> dict[str, object]:
    """Build a read-only snapshot artifact without transport-private values."""

    return {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "rf_source.snapshot",
        "snapshot": rf_source_snapshot_document(snapshot),
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
    "RF_SOURCE_OPERATION_ARTIFACT_SCHEMA",
    "RF_SOURCE_SNAPSHOT_MIN_CORE_VERSION",
    "RF_SOURCE_SNAPSHOT_SCHEMA",
    "RfAvailability",
    "RfCwProfile",
    "RfCwRequest",
    "RfCwResult",
    "RfFeature",
    "RfFeatureCapability",
    "RfFeatureDirection",
    "RfFeatureProfile",
    "RfModulationProfile",
    "RfModulationState",
    "RfObserved",
    "RfOutputPortProfile",
    "RfOutputProfile",
    "RfOutputRequest",
    "RfOutputResult",
    "RfPortSnapshot",
    "RfProtectionConditionPolicy",
    "RfProtectionStatus",
    "RfPulseProfile",
    "RfPulseState",
    "RfReasonCode",
    "RfSourceDescriptorExtensions",
    "RfSourceDriver",
    "RfSourceSnapshot",
    "RfSourceTopology",
    "RfSweepProfile",
    "RfSweepState",
    "rf_source_canonical_json",
    "rf_source_cw_operation_artifact",
    "rf_source_digest",
    "rf_source_snapshot_document",
    "rf_source_snapshot_operation_artifact",
    "rf_source_output_operation_artifact",
    "rf_source_to_data",
]
