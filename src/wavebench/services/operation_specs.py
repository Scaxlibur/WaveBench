"""Central metadata for public WaveBench operations.

The registry is deliberately side-effect free.  It describes an operation's
capability, risk and resource semantics so that run checks, access policy,
leases and future explain/intent commands can consume one source of truth.
It does not open instruments or call a Service.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from wavebench.errors import ConfigError


OperationEffect = Literal[
    "offline",
    "observe",
    "stateful_read",
    "write",
    "acquire",
]
LeaseMode = Literal["none", "shared", "exclusive"]

_EFFECTS = frozenset({"offline", "observe", "stateful_read", "write", "acquire"})
_LEASE_MODES = frozenset({"none", "shared", "exclusive"})
_INSTRUMENT_KINDS = frozenset({"scope", "source", "power", "dmm", "sweep_analyzer"})


@dataclass(frozen=True)
class OperationSpec:
    """Immutable safety metadata for one public operation."""

    operation: str
    instrument_kind: str | None
    required_capabilities: tuple[str, ...] = ()
    optional_capabilities: tuple[str, ...] = ()
    effect: OperationEffect = "observe"
    lease_mode: LeaseMode = "exclusive"
    changed_fields: tuple[str, ...] = ()
    restore_coverage: str = "none"
    risk_flags: tuple[str, ...] = ()
    safe_alternatives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        operation = self.operation.strip()
        if not operation or operation != self.operation:
            raise ValueError("operation name must be non-empty and trimmed")
        if self.instrument_kind is not None and self.instrument_kind not in _INSTRUMENT_KINDS:
            raise ValueError(f"unsupported instrument kind: {self.instrument_kind!r}")
        if self.effect not in _EFFECTS:
            raise ValueError(f"unsupported operation effect: {self.effect!r}")
        if self.lease_mode not in _LEASE_MODES:
            raise ValueError(f"unsupported operation lease mode: {self.lease_mode!r}")
        for name, values in (
            ("required_capabilities", self.required_capabilities),
            ("optional_capabilities", self.optional_capabilities),
            ("changed_fields", self.changed_fields),
            ("risk_flags", self.risk_flags),
            ("safe_alternatives", self.safe_alternatives),
        ):
            if any(not value or value.strip() != value for value in values):
                raise ValueError(f"{name} entries must be non-empty and trimmed")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} entries must not contain duplicates")
        overlap = set(self.required_capabilities) & set(self.optional_capabilities)
        if overlap:
            raise ValueError(
                "required and optional capabilities overlap: " + ", ".join(sorted(overlap))
            )

    @property
    def mutates(self) -> bool:
        """Whether the operation may change instrument state or acquire data."""

        return self.effect in {"write", "acquire"}

    def as_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "instrument_kind": self.instrument_kind,
            "required_capabilities": list(self.required_capabilities),
            "optional_capabilities": list(self.optional_capabilities),
            "effect": self.effect,
            "lease_mode": self.lease_mode,
            "changed_fields": list(self.changed_fields),
            "restore_coverage": self.restore_coverage,
            "risk_flags": list(self.risk_flags),
            "safe_alternatives": list(self.safe_alternatives),
        }


class OperationRegistry:
    """Read-only operation registry with actionable unknown-operation errors."""

    def __init__(self, specs: Mapping[str, OperationSpec]) -> None:
        copied = dict(specs)
        if set(copied) != {spec.operation for spec in copied.values()}:
            raise ValueError("operation registry keys must match spec.operation")
        object.__setattr__(self, "_specs", MappingProxyType(copied))

    def get(self, operation: str) -> OperationSpec | None:
        return self._specs.get(operation)

    def require(self, operation: str) -> OperationSpec:
        spec = self.get(operation)
        if spec is None:
            raise ConfigError(f"unknown WaveBench operation: {operation!r}")
        return spec

    def all(self, *, instrument_kind: str | None = None) -> tuple[OperationSpec, ...]:
        if instrument_kind is not None and instrument_kind not in _INSTRUMENT_KINDS:
            raise ConfigError(f"unsupported instrument kind: {instrument_kind!r}")
        return tuple(
            spec
            for spec in self._specs.values()
            if instrument_kind is None or spec.instrument_kind == instrument_kind
        )


def _spec(
    operation: str,
    instrument_kind: str | None,
    *,
    required_capabilities: tuple[str, ...] = (),
    optional_capabilities: tuple[str, ...] = (),
    effect: OperationEffect = "observe",
    lease_mode: LeaseMode = "exclusive",
    changed_fields: tuple[str, ...] = (),
    restore_coverage: str = "none",
    risk_flags: tuple[str, ...] = (),
    safe_alternatives: tuple[str, ...] = (),
) -> OperationSpec:
    return OperationSpec(
        operation=operation,
        instrument_kind=instrument_kind,
        required_capabilities=required_capabilities,
        optional_capabilities=optional_capabilities,
        effect=effect,
        lease_mode=lease_mode,
        changed_fields=changed_fields,
        restore_coverage=restore_coverage,
        risk_flags=risk_flags,
        safe_alternatives=safe_alternatives,
    )


_BUILTIN_SPECS = (
    _spec("run.schema", None, effect="offline", lease_mode="none"),
    _spec("run.check", None, effect="offline", lease_mode="none"),
    _spec("run.report", None, effect="offline", lease_mode="none"),
    _spec("run.compare", None, effect="offline", lease_mode="none"),
    _spec("run.resume", None, effect="offline", lease_mode="none"),
    _spec("scope.idn", "scope", required_capabilities=("scope.idn",), effect="observe"),
    _spec("scope.errors", "scope", required_capabilities=("scope.errors",), effect="stateful_read"),
    _spec("scope.status", "scope", required_capabilities=("scope.snapshot",), effect="stateful_read"),
    _spec("scope.acquisition_status", "scope", required_capabilities=("scope.acquisition_status",), effect="stateful_read"),
    _spec("scope.channel_coupling", "scope", required_capabilities=("scope.channel_coupling",), effect="stateful_read"),
    _spec(
        "scope.autoscale",
        "scope",
        required_capabilities=("scope.autoscale",),
        effect="write",
        changed_fields=("timebase", "vertical_scale", "trigger"),
        risk_flags=("front_panel_state",),
        safe_alternatives=("scope.capture",),
    ),
    _spec(
        "scope.capture",
        "scope",
        required_capabilities=("scope.capture_waveform",),
        effect="acquire",
        changed_fields=("acquisition", "waveform_package"),
        risk_flags=("trigger", "acquisition_state"),
    ),
    _spec(
        "scope.capture_waveforms",
        "scope",
        required_capabilities=("scope.capture_waveforms",),
        effect="acquire",
        changed_fields=("acquisition", "waveform_package"),
        risk_flags=("trigger", "acquisition_state"),
    ),
    _spec("scope.capture_multiple", "scope", required_capabilities=("scope.capture_waveforms",), effect="acquire", changed_fields=("acquisition", "waveform_package"), risk_flags=("trigger", "acquisition_state")),
    _spec("scope.fetch_waveform", "scope", required_capabilities=("scope.fetch_waveform",), effect="acquire", changed_fields=("waveform_package",), risk_flags=("acquisition_state",)),
    _spec("scope.capture_average", "scope", required_capabilities=("scope.capture_average",), effect="acquire", changed_fields=("acquisition", "waveform_package"), risk_flags=("trigger", "acquisition_state")),
    _spec("scope.digital_status", "scope", required_capabilities=("scope.digital_status",), effect="stateful_read"),
    _spec("scope.digital_waveform", "scope", required_capabilities=("scope.digital_waveform",), effect="acquire", changed_fields=("acquisition", "waveform_package"), risk_flags=("trigger", "acquisition_state")),
    _spec("scope.history_timestamps", "scope", required_capabilities=("scope.history_timestamps",), effect="stateful_read"),
    _spec("scope.measurement_statistics", "scope", required_capabilities=("scope.measurement_statistics",), effect="stateful_read"),
    _spec("scope.math_metadata", "scope", required_capabilities=("scope.math_metadata",), effect="stateful_read"),
    _spec("scope.fft_status", "scope", required_capabilities=("scope.fft_status",), effect="stateful_read"),
    _spec("scope.reference_metadata", "scope", required_capabilities=("scope.reference_metadata",), effect="stateful_read"),
    _spec("scope.cursor_readout", "scope", required_capabilities=("scope.cursor_readout",), effect="stateful_read"),
    _spec("source.idn", "source", required_capabilities=("source.idn",), effect="observe"),
    _spec("source.errors", "source", required_capabilities=("source.errors",), effect="stateful_read"),
    _spec("source.status", "source", required_capabilities=("source.status",), effect="stateful_read"),
    _spec("source.channel_profile", "source", required_capabilities=("source.channel_profile",), effect="stateful_read"),
    _spec("source.coupling_profile", "source", required_capabilities=("source.coupling_profile",), effect="stateful_read"),
    _spec("source.coupling_configure", "source", required_capabilities=("source.coupling_configure",), effect="write", changed_fields=("coupling",), risk_flags=("state_drift",)),
    _spec("source.harmonic_profile", "source", required_capabilities=("source.harmonic_profile",), effect="stateful_read"),
    _spec("source.harmonic_configure", "source", required_capabilities=("source.harmonic_configure",), effect="write", changed_fields=("harmonic_profile",), risk_flags=("signal_output", "state_drift")),
    _spec("source.modulation_am_profile", "source", required_capabilities=("source.modulation_am_profile",), effect="stateful_read"),
    _spec("source.modulation_am_configure", "source", required_capabilities=("source.modulation_am_configure",), effect="write", changed_fields=("am_modulation",), risk_flags=("signal_output", "state_drift")),
    _spec("source.modulation_fm_profile", "source", required_capabilities=("source.modulation_fm_profile",), effect="stateful_read"),
    _spec("source.modulation_fm_configure", "source", required_capabilities=("source.modulation_fm_configure",), effect="write", changed_fields=("fm_modulation",), risk_flags=("signal_output", "state_drift")),
    _spec("source.modulation_pm_profile", "source", required_capabilities=("source.modulation_pm_profile",), effect="stateful_read"),
    _spec("source.modulation_pm_configure", "source", required_capabilities=("source.modulation_pm_configure",), effect="write", changed_fields=("pm_modulation",), risk_flags=("signal_output", "state_drift")),
    _spec("source.modulation_pwm_profile", "source", required_capabilities=("source.modulation_pwm_profile",), effect="stateful_read"),
    _spec("source.modulation_pwm_configure", "source", required_capabilities=("source.modulation_pwm_configure",), effect="write", changed_fields=("pwm_modulation",), risk_flags=("signal_output", "state_drift")),
    _spec("source.pulse_profile", "source", required_capabilities=("source.pulse_profile",), effect="stateful_read"),
    _spec("source.pulse_configure", "source", required_capabilities=("source.pulse_configure",), effect="write", changed_fields=("pulse_profile",), risk_flags=("signal_output", "state_drift")),
    _spec("source.burst_profile", "source", required_capabilities=("source.burst_profile",), effect="stateful_read"),
    _spec("source.burst_configure", "source", required_capabilities=("source.burst_configure",), effect="write", changed_fields=("burst_profile",), risk_flags=("signal_output", "state_drift")),
    _spec("source.burst_trigger", "source", required_capabilities=("source.burst_trigger",), effect="write", changed_fields=("trigger",), risk_flags=("trigger", "signal_output")),
    _spec("source.sweep_profile", "source", required_capabilities=("source.sweep_profile",), effect="stateful_read"),
    _spec("source.sweep_configure", "source", required_capabilities=("source.sweep_configure",), effect="write", changed_fields=("sweep_profile",), risk_flags=("signal_output", "state_drift")),
    _spec("source.sweep_trigger", "source", required_capabilities=("source.sweep_trigger",), effect="write", changed_fields=("trigger",), risk_flags=("trigger", "signal_output")),
    _spec("source.counter_profile", "source", required_capabilities=("source.counter_profile",), effect="stateful_read"),
    _spec(
        "source.set_frequency",
        "source",
        required_capabilities=("source.set_frequency",),
        effect="write",
        changed_fields=("frequency", "mode"),
        restore_coverage="basic",
        risk_flags=("signal_output", "state_drift"),
        safe_alternatives=("source.status",),
    ),
    _spec(
        "source.set_function",
        "source",
        required_capabilities=("source.set_function",),
        effect="write",
        changed_fields=("function",),
        restore_coverage="basic",
        risk_flags=("signal_output", "state_drift"),
    ),
    _spec(
        "source.set_amplitude_vpp",
        "source",
        required_capabilities=("source.set_amplitude_vpp",),
        effect="write",
        changed_fields=("amplitude_vpp",),
        restore_coverage="basic",
        risk_flags=("signal_level", "signal_output"),
        safe_alternatives=("source.status",),
    ),
    _spec(
        "source.output",
        "source",
        required_capabilities=("source.output",),
        effect="write",
        changed_fields=("output",),
        restore_coverage="basic",
        risk_flags=("dangerous_output",),
        safe_alternatives=("source.status",),
    ),
    _spec("source.set_square_duty_cycle", "source", required_capabilities=("source.set_square_duty_cycle",), effect="write", changed_fields=("square_duty_cycle",), restore_coverage="basic", risk_flags=("signal_output", "state_drift")),
    _spec("source.arbitrary_probe", "source", required_capabilities=("source.arbitrary_probe",), effect="stateful_read"),
    _spec("source.arbitrary_upload", "source", required_capabilities=("source.arbitrary_upload",), effect="write", changed_fields=("arbitrary_payload",), risk_flags=("signal_output", "volatile_payload")),
    _spec("power.idn", "power", required_capabilities=("power.idn",), effect="observe"),
    _spec("power.status", "power", required_capabilities=("power.status",), effect="stateful_read"),
    _spec("power.measurement", "power", required_capabilities=("power.measurement",), effect="stateful_read"),
    _spec("power.protection_status", "power", required_capabilities=("power.protection",), effect="stateful_read"),
    _spec("power.set_protection", "power", required_capabilities=("power.protection",), effect="write", changed_fields=("ovp", "ocp"), risk_flags=("power_protection", "state_drift")),
    _spec(
        "power.set_voltage_current_limit",
        "power",
        required_capabilities=("power.set_voltage_current_limit",),
        effect="write",
        changed_fields=("set_voltage", "set_current"),
        risk_flags=("power_level", "state_drift"),
        safe_alternatives=("power.status",),
    ),
    _spec(
        "power.output",
        "power",
        required_capabilities=("power.output",),
        effect="write",
        changed_fields=("output",),
        risk_flags=("dangerous_output",),
        safe_alternatives=("power.status",),
    ),
    _spec("dmm.idn", "dmm", required_capabilities=("dmm.idn",), effect="observe"),
    _spec("dmm.read", "dmm", required_capabilities=("dmm.read",), effect="stateful_read"),
    _spec("dmm.function_status", "dmm", required_capabilities=("dmm.function_status",), effect="stateful_read"),
    _spec("dmm.set_function", "dmm", required_capabilities=("dmm.set_function",), effect="write", changed_fields=("function",), risk_flags=("state_drift",)),
    _spec("dmm.measurement_profile", "dmm", required_capabilities=("dmm.measurement_profile",), effect="stateful_read"),
    _spec("dmm.trigger_status", "dmm", required_capabilities=("dmm.trigger_status",), effect="stateful_read"),
    _spec("dmm.calculation_status", "dmm", required_capabilities=("dmm.calculation_status",), effect="stateful_read"),
    _spec("dmm.calculation_statistics", "dmm", required_capabilities=("dmm.calculation_statistics",), effect="stateful_read"),
    _spec("dmm.system_interface_status", "dmm", required_capabilities=("dmm.system_interface_status",), effect="stateful_read"),
    _spec("dmm.set_voltage_range", "dmm", required_capabilities=("dmm.set_voltage_range",), effect="write", changed_fields=("voltage_range",), risk_flags=("state_drift",)),
    _spec("dmm.set_dcv_impedance", "dmm", required_capabilities=("dmm.set_dcv_impedance",), effect="write", changed_fields=("dcv_impedance",), risk_flags=("state_drift",)),
)

OPERATION_REGISTRY = OperationRegistry({spec.operation: spec for spec in _BUILTIN_SPECS})


def get_operation_spec(operation: str) -> OperationSpec | None:
    """Return metadata for an operation, or ``None`` when it is not registered."""

    return OPERATION_REGISTRY.get(operation)


def require_operation_spec(operation: str) -> OperationSpec:
    return OPERATION_REGISTRY.require(operation)


def list_operation_specs(*, instrument_kind: str | None = None) -> tuple[OperationSpec, ...]:
    return OPERATION_REGISTRY.all(instrument_kind=instrument_kind)
