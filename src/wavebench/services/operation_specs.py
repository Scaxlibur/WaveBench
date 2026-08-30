"""Central metadata for public WaveBench operations.

The registry is deliberately side-effect free.  It describes an operation's
capability, risk and resource semantics so that run checks, access policy,
leases and future explain/intent commands can consume one source of truth.
It does not open instruments or call a Service.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Mapping

from wavebench.errors import ConfigError
from wavebench.scope_extension_constants import (
    SCOPE_AVERAGE_CAPTURE_V2_BINARY_OPERATION_MAX_BYTES,
    SCOPE_AVERAGE_CAPTURE_V2_BINARY_QUERY_MAX_COUNT,
    SCOPE_AVERAGE_CAPTURE_V2_BINARY_RESPONSE_MAX_BYTES,
    SCOPE_AVERAGE_CAPTURE_V2_BINARY_RESYNCHRONIZATION_MAX_BYTES,
    SCOPE_AVERAGE_CAPTURE_V2_OPERATION_TIMEOUT_MS,
    SCOPE_ACQUISITION_OPERATION_TIMEOUT_MS,
    SCOPE_ACQUISITION_STATUS_V2_OPERATION_TIMEOUT_MS,
    SCOPE_CURSOR_READOUT_V2_OPERATION_TIMEOUT_MS,
    SCOPE_MEASUREMENT_STATISTICS_V2_OPERATION_TIMEOUT_MS,
    SCOPE_FFT_STATUS_V2_OPERATION_TIMEOUT_MS,
    SCOPE_PROFILE_OPERATION_TIMEOUT_MS,
    SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES,
    SCOPE_SCREENSHOT_BINARY_QUERY_MAX_COUNT,
    SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES,
    SCOPE_SCREENSHOT_BINARY_RESYNCHRONIZATION_MAX_BYTES,
    SCOPE_SCREENSHOT_OPERATION_TIMEOUT_MS,
    SCOPE_SNAPSHOT_V2_OPERATION_TIMEOUT_MS,
    SCOPE_TRACE_BINARY_OPERATION_MAX_BYTES,
    SCOPE_TRACE_BINARY_QUERY_MAX_COUNT,
    SCOPE_TRACE_BINARY_RESPONSE_MAX_BYTES,
    SCOPE_TRACE_BINARY_RESYNCHRONIZATION_MAX_BYTES,
    SCOPE_TRACE_OPERATION_TIMEOUT_MS,
)

if TYPE_CHECKING:
    from wavebench.instruments.scope_extensions import ScopeEmbeddedScreenshotContract


OperationEffect = Literal[
    "offline",
    "observe",
    "stateful_read",
    "write",
    "acquire",
]
SessionPurpose = Literal["normal", "recovery", "verification", "lifecycle"]
LeaseMode = Literal["none", "shared", "exclusive"]
ErrorCheckMinimum = Literal["required", "if_supported", "disabled"]

_EFFECTS = frozenset({"offline", "observe", "stateful_read", "write", "acquire"})
_LEASE_MODES = frozenset({"none", "shared", "exclusive"})
_INSTRUMENT_KINDS = frozenset(
    {"scope", "source", "rf_source", "power", "dmm", "sweep_analyzer"}
)
_SESSION_PURPOSES = frozenset({"normal", "recovery", "verification", "lifecycle"})
_ERROR_CHECK_MINIMUMS = frozenset({"required", "if_supported", "disabled"})


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
    session_purpose: SessionPurpose = "normal"
    required_verified_fields: tuple[str, ...] = ()
    verification_fields: tuple[str, ...] = ()
    postcondition_fields: tuple[str, ...] = ()
    cleanup_verification_fields: tuple[str, ...] = ()
    timeout_source: str = "connection.timeout_ms"
    operation_timeout_ms: int | None = None
    binary_response_max_bytes: int | None = None
    binary_operation_max_bytes: int | None = None
    binary_query_max_count: int | None = None
    binary_resynchronization_max_bytes: int | None = None
    error_check_minimum: ErrorCheckMinimum | None = None
    embedded_screenshot_contract: "ScopeEmbeddedScreenshotContract | None" = None
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
        if self.session_purpose not in _SESSION_PURPOSES:
            raise ValueError(f"unsupported session purpose: {self.session_purpose!r}")
        if not self.timeout_source or self.timeout_source.strip() != self.timeout_source:
            raise ValueError("timeout_source must be non-empty and trimmed")
        if self.operation_timeout_ms is not None and (
            isinstance(self.operation_timeout_ms, bool)
            or not isinstance(self.operation_timeout_ms, int)
            or self.operation_timeout_ms < 1
        ):
            raise ValueError("operation_timeout_ms must be a positive integer")
        if self.timeout_source == "operation.timeout_ms" and self.operation_timeout_ms is None:
            raise ValueError("operation.timeout_ms requires an explicit operation_timeout_ms")
        if self.operation_timeout_ms is not None and self.timeout_source != "operation.timeout_ms":
            raise ValueError("explicit operation timeout must use timeout_source='operation.timeout_ms'")
        if (
            self.error_check_minimum is not None
            and self.error_check_minimum not in _ERROR_CHECK_MINIMUMS
        ):
            raise ValueError(f"unsupported error check minimum: {self.error_check_minimum!r}")
        for name, values in (
            ("required_capabilities", self.required_capabilities),
            ("optional_capabilities", self.optional_capabilities),
            ("changed_fields", self.changed_fields),
            ("required_verified_fields", self.required_verified_fields),
            ("verification_fields", self.verification_fields),
            ("postcondition_fields", self.postcondition_fields),
            ("cleanup_verification_fields", self.cleanup_verification_fields),
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
        binary_limits = (
            self.binary_response_max_bytes,
            self.binary_operation_max_bytes,
            self.binary_query_max_count,
            self.binary_resynchronization_max_bytes,
        )
        if any(value is not None for value in binary_limits):
            if any(value is None for value in binary_limits):
                raise ValueError("binary operations must define all four binary limits")
            for label, value in (
                ("binary_response_max_bytes", self.binary_response_max_bytes),
                ("binary_operation_max_bytes", self.binary_operation_max_bytes),
                ("binary_query_max_count", self.binary_query_max_count),
            ):
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(f"{label} must be a positive integer")
            if (
                isinstance(self.binary_resynchronization_max_bytes, bool)
                or not isinstance(self.binary_resynchronization_max_bytes, int)
                or self.binary_resynchronization_max_bytes < 0
            ):
                raise ValueError(
                    "binary_resynchronization_max_bytes must be a non-negative integer"
                )
            assert self.binary_response_max_bytes is not None
            assert self.binary_operation_max_bytes is not None
            if self.binary_operation_max_bytes < self.binary_response_max_bytes:
                raise ValueError("binary operation limit must cover at least one response")
        if self.embedded_screenshot_contract is not None:
            from wavebench.instruments.scope_extensions import ScopeEmbeddedScreenshotContract

            contract = self.embedded_screenshot_contract
            if not isinstance(contract, ScopeEmbeddedScreenshotContract):
                raise TypeError("embedded_screenshot_contract has an invalid type")
            if self.effect != "acquire":
                raise ValueError("embedded screenshots are only valid for acquire operations")
            if not set(contract.changed_fields + contract.output_fields) <= set(self.changed_fields):
                raise ValueError("operation changed_fields do not cover embedded screenshot effects")
            if not set(contract.verification_fields) <= set(self.verification_fields):
                raise ValueError(
                    "operation verification_fields do not cover embedded screenshot state"
                )
            if not set(contract.cleanup_verification_fields) <= set(
                self.cleanup_verification_fields
            ):
                raise ValueError(
                    "operation cleanup fields do not cover embedded screenshot state"
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
            "session_purpose": self.session_purpose,
            "required_verified_fields": list(self.required_verified_fields),
            "verification_fields": list(self.verification_fields),
            "postcondition_fields": list(self.postcondition_fields),
            "cleanup_verification_fields": list(self.cleanup_verification_fields),
            "timeout_source": self.timeout_source,
            "operation_timeout_ms": self.operation_timeout_ms,
            "binary_response_max_bytes": self.binary_response_max_bytes,
            "binary_operation_max_bytes": self.binary_operation_max_bytes,
            "binary_query_max_count": self.binary_query_max_count,
            "binary_resynchronization_max_bytes": self.binary_resynchronization_max_bytes,
            "error_check_minimum": self.error_check_minimum,
            "embedded_screenshot_contract": (
                asdict(self.embedded_screenshot_contract)
                if self.embedded_screenshot_contract is not None
                else None
            ),
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
    session_purpose: SessionPurpose = "normal",
    required_verified_fields: tuple[str, ...] = (),
    verification_fields: tuple[str, ...] = (),
    postcondition_fields: tuple[str, ...] = (),
    cleanup_verification_fields: tuple[str, ...] = (),
    timeout_source: str = "connection.timeout_ms",
    operation_timeout_ms: int | None = None,
    binary_response_max_bytes: int | None = None,
    binary_operation_max_bytes: int | None = None,
    binary_query_max_count: int | None = None,
    binary_resynchronization_max_bytes: int | None = None,
    error_check_minimum: ErrorCheckMinimum | None = None,
    embedded_screenshot_contract: "ScopeEmbeddedScreenshotContract | None" = None,
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
        session_purpose=session_purpose,
        required_verified_fields=required_verified_fields,
        verification_fields=verification_fields,
        postcondition_fields=postcondition_fields,
        cleanup_verification_fields=cleanup_verification_fields,
        timeout_source=timeout_source,
        operation_timeout_ms=operation_timeout_ms,
        binary_response_max_bytes=binary_response_max_bytes,
        binary_operation_max_bytes=binary_operation_max_bytes,
        binary_query_max_count=binary_query_max_count,
        binary_resynchronization_max_bytes=binary_resynchronization_max_bytes,
        error_check_minimum=error_check_minimum,
        embedded_screenshot_contract=embedded_screenshot_contract,
        risk_flags=risk_flags,
        safe_alternatives=safe_alternatives,
    )


_SCOPE_CAPTURE_TRANSFER_STATE_FIELDS = (
    "scope.query_response_header",
    "scope.waveform_format",
    "scope.waveform_byte_order",
    "scope.waveform_points",
    # This is one atomic transfer-selection state.  It includes any stride,
    # point-count, first-point and segment selectors exposed as one setup.
    "scope.waveform_transfer_window",
)
_SCOPE_CAPTURE_CHANGED_FIELDS = (
    "scope.run_state",
    "scope.acquisition",
    "scope.trigger",
    "scope.timebase",
    "scope.channel_display",
    "scope.channel_vertical",
    "scope.waveform_source",
    "scope.waveform_mode",
    *_SCOPE_CAPTURE_TRANSFER_STATE_FIELDS,
    "scope.error_queue",
    "scope.capture_identity",
    "output.waveform_package",
)
_SCOPE_CAPTURE_VERIFICATION_FIELDS = (
    "scope.identity",
    "scope.run_state",
    "scope.timebase",
    "scope.channel_display",
    "scope.channel_vertical",
    "scope.waveform_source",
    "scope.waveform_mode",
    *_SCOPE_CAPTURE_TRANSFER_STATE_FIELDS,
    "scope.capture_identity",
)
_SCOPE_AVERAGE_CAPTURE_RESTORE_FIELDS = (
    "scope.run_state",
    "scope.acquisition",
    "scope.trigger",
    "scope.timebase",
    "scope.channel_display",
    "scope.channel_vertical",
    "scope.waveform_source",
    "scope.waveform_mode",
    "scope.query_response_header",
    "scope.waveform_format",
    "scope.waveform_byte_order",
    "scope.waveform_points",
    "scope.waveform_transfer_window",
)
_SCOPE_AVERAGE_CAPTURE_GRANULAR_FIELDS = (
    "scope.acquisition.type",
    "scope.acquisition.average_count",
)
_SCOPE_AVERAGE_CAPTURE_CHANGED_FIELDS = (
    *_SCOPE_AVERAGE_CAPTURE_RESTORE_FIELDS,
    *_SCOPE_AVERAGE_CAPTURE_GRANULAR_FIELDS,
    "scope.error_queue",
)


_BUILTIN_SPECS = (
    _spec("run.schema", None, effect="offline", lease_mode="none"),
    _spec("run.check", None, effect="offline", lease_mode="none"),
    _spec("run.intent", None, effect="offline", lease_mode="none"),
    _spec("run.sleep", None, effect="offline", lease_mode="none"),
    _spec("lock.status", None, effect="offline", lease_mode="none"),
    _spec("run.report", None, effect="offline", lease_mode="none"),
    _spec("run.compare", None, effect="offline", lease_mode="none"),
    _spec("run.resume", None, effect="offline", lease_mode="none"),
    _spec("scope.idn", "scope", required_capabilities=("scope.idn",), effect="observe"),
    _spec(
        "scope.errors",
        "scope",
        required_capabilities=("scope.errors",),
        effect="stateful_read",
        changed_fields=("scope.error_queue",),
    ),
    _spec(
        "scope.status",
        "scope",
        required_capabilities=("scope.idn",),
        optional_capabilities=("scope.snapshot", "scope.channel_coupling"),
        effect="stateful_read",
        safe_alternatives=("scope.idn", "scope.channel_coupling"),
    ),
    _spec(
        "scope.channel_input_state_v2",
        "scope",
        required_capabilities=("scope.channel_input_state_v2",),
        effect="stateful_read",
        lease_mode="exclusive",
    ),
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
        changed_fields=_SCOPE_CAPTURE_CHANGED_FIELDS,
        restore_coverage="capture-baseline-only",
        required_verified_fields=("scope.identity",),
        verification_fields=_SCOPE_CAPTURE_VERIFICATION_FIELDS,
        risk_flags=("trigger", "acquisition_state", "temporary_transfer_setup"),
    ),
    _spec(
        "scope.capture_waveforms",
        "scope",
        required_capabilities=("scope.capture_waveforms",),
        effect="acquire",
        changed_fields=_SCOPE_CAPTURE_CHANGED_FIELDS,
        restore_coverage="capture-baseline-only",
        required_verified_fields=("scope.identity",),
        verification_fields=_SCOPE_CAPTURE_VERIFICATION_FIELDS,
        risk_flags=("trigger", "acquisition_state", "temporary_transfer_setup"),
    ),
    _spec("scope.capture_multiple", "scope", required_capabilities=("scope.capture_waveforms",), effect="acquire", changed_fields=_SCOPE_CAPTURE_CHANGED_FIELDS, restore_coverage="capture-baseline-only", required_verified_fields=("scope.identity",), verification_fields=_SCOPE_CAPTURE_VERIFICATION_FIELDS, risk_flags=("trigger", "acquisition_state", "temporary_transfer_setup")),
    _spec("scope.fetch_waveform", "scope", required_capabilities=("scope.fetch_waveform",), effect="acquire", changed_fields=_SCOPE_CAPTURE_CHANGED_FIELDS, restore_coverage="capture-baseline-only", required_verified_fields=("scope.identity",), verification_fields=_SCOPE_CAPTURE_VERIFICATION_FIELDS, risk_flags=("acquisition_state", "temporary_transfer_setup")),
    _spec("scope.capture_average", "scope", required_capabilities=("scope.capture_average",), effect="acquire", changed_fields=("acquisition", "waveform_package"), risk_flags=("trigger", "acquisition_state")),
    _spec("scope.digital_status", "scope", required_capabilities=("scope.digital_status",), effect="stateful_read"),
    _spec(
        "scope.digital_status_v2",
        "scope",
        required_capabilities=("scope.digital_status_v2",),
        effect="stateful_read",
        lease_mode="exclusive",
    ),
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
    _spec(
        "source.snapshot_v2",
        "source",
        required_capabilities=("source.snapshot_v2",),
        effect="stateful_read",
        lease_mode="exclusive",
        restore_coverage="none-read-only",
        session_purpose="normal",
        verification_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
        ),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("state_dependent_query",),
    ),
    _spec(
        "source.basic_configure_v2",
        "source",
        required_capabilities=("source.basic_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.basic",),
        restore_coverage="source-v2-basic",
        required_verified_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
        ),
        postcondition_fields=("source.channel.basic",),
        cleanup_verification_fields=("source.channel.basic", "source.channel.output"),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off"),
    ),
    _spec(
        "source.basic_live_configure_v2",
        "source",
        required_capabilities=(
            "source.basic_live_configure_v2",
            "source.basic_configure_v2",
            "source.output_v2",
        ),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.basic",),
        restore_coverage="source-v2-live-basic",
        required_verified_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
        ),
        postcondition_fields=(
            "source.channel.basic",
            "source.channel.output",
        ),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_on", "live_signal_mutation"),
    ),
    _spec(
        "source.harmonics_configure_v2",
        "source",
        required_capabilities=("source.harmonics_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.harmonics",),
        restore_coverage="source-v2-harmonics",
        required_verified_fields=(
            "source.identity",
            "source.channel.harmonics",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.harmonics",
            "source.channel.output",
        ),
        postcondition_fields=("source.channel.harmonics", "source.channel.output"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off"),
    ),
    _spec(
        "source.harmonics_disable_v2",
        "source",
        required_capabilities=("source.harmonics_disable_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.harmonics",),
        restore_coverage="source-v2-harmonics",
        required_verified_fields=(
            "source.identity",
            "source.channel.harmonics",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.harmonics",
            "source.channel.output",
        ),
        postcondition_fields=("source.channel.harmonics", "source.channel.output"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off"),
    ),
    _spec(
        "source.modulation_configure_v2",
        "source",
        required_capabilities=("source.modulation_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.modulation",),
        restore_coverage="source-v2-modulation",
        required_verified_fields=(
            "source.identity",
            "source.channel.modulation",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.modulation",
            "source.channel.output",
        ),
        postcondition_fields=("source.channel.modulation", "source.channel.output"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "am_internal_only"),
    ),
    _spec(
        "source.modulation_pm_configure_v2",
        "source",
        required_capabilities=("source.modulation_pm_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.modulation",),
        restore_coverage="source-v2-modulation-pm",
        required_verified_fields=(
            "source.identity",
            "source.channel.modulation",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.modulation",
            "source.channel.output",
        ),
        postcondition_fields=("source.channel.modulation", "source.channel.output"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "pm_internal_only"),
    ),
    _spec(
        "source.modulation_fm_configure_v2",
        "source",
        required_capabilities=("source.modulation_fm_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.modulation",),
        restore_coverage="source-v2-modulation-fm",
        required_verified_fields=(
            "source.identity",
            "source.channel.modulation",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.modulation",
            "source.channel.output",
        ),
        postcondition_fields=("source.channel.modulation", "source.channel.output"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "fm_internal_only"),
    ),
    _spec(
        "source.modulation_pwm_configure_v2",
        "source",
        required_capabilities=("source.modulation_pwm_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.modulation",),
        restore_coverage="source-v2-modulation-pwm",
        required_verified_fields=(
            "source.identity",
            "source.channel.modulation",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.modulation",
            "source.channel.output",
        ),
        postcondition_fields=("source.channel.modulation", "source.channel.output"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "pwm_internal_only"),
    ),
    _spec(
        "source.sweep_configure_v2",
        "source",
        required_capabilities=("source.sweep_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.basic", "source.channel.sweep"),
        restore_coverage="source-v2-sweep",
        required_verified_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
            "source.channel.sweep",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
            "source.channel.sweep",
        ),
        postcondition_fields=(
            "source.channel.basic",
            "source.channel.output",
            "source.channel.sweep",
        ),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "sweep_internal_no_fire"),
    ),
    _spec(
        "source.sweep_fire_v2",
        "source",
        required_capabilities=(
            "source.sweep_fire_v2",
            "source.sweep_configure_v2",
            "source.output_v2",
        ),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.sweep",),
        restore_coverage="source-v2-sweep-fire",
        required_verified_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
            "source.channel.sweep",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
            "source.channel.sweep",
        ),
        postcondition_fields=("source.channel.output", "source.channel.sweep"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=(
            "source_v2",
            "persistent_session_required",
            "output_must_be_on",
            "emits_signal",
            "no_retry",
        ),
    ),
    _spec(
        "source.burst_configure_v2",
        "source",
        required_capabilities=("source.burst_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.burst",),
        restore_coverage="source-v2-burst",
        required_verified_fields=(
            "source.identity",
            "source.channel.burst",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.burst",
            "source.channel.output",
        ),
        postcondition_fields=("source.channel.burst", "source.channel.output"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "burst_internal_triggered_only"),
    ),
    _spec(
        "source.burst_fire_v2",
        "source",
        required_capabilities=(
            "source.burst_fire_v2",
            "source.burst_configure_v2",
            "source.output_v2",
        ),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.burst",),
        restore_coverage="source-v2-burst-fire",
        required_verified_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.burst",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.burst",
            "source.channel.output",
        ),
        postcondition_fields=("source.channel.burst", "source.channel.output"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=(
            "source_v2",
            "persistent_session_required",
            "output_must_be_on",
            "emits_signal",
            "no_retry",
        ),
    ),
    _spec(
        "source.pulse_configure_v2",
        "source",
        required_capabilities=("source.pulse_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.pulse",),
        restore_coverage="source-v2-pulse",
        required_verified_fields=(
            "source.identity",
            "source.channel.output",
            "source.channel.pulse",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.output",
            "source.channel.pulse",
        ),
        postcondition_fields=("source.channel.output", "source.channel.pulse"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "pulse_width_only"),
    ),
    _spec(
        "source.arbitrary_storage_v2",
        "source",
        required_capabilities=("source.arbitrary_storage_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.arbitrary_storage",),
        restore_coverage="source-v2-arbitrary-storage",
        required_verified_fields=(
            "source.identity",
            "source.channel.arbitrary_selection",
            "source.channel.arbitrary_storage",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.arbitrary_selection",
            "source.channel.arbitrary_storage",
            "source.channel.output",
        ),
        postcondition_fields=(
            "source.channel.arbitrary_selection",
            "source.channel.arbitrary_storage",
            "source.channel.output",
        ),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "arbitrary_storage", "payload_not_artifact"),
    ),
    _spec(
        "source.arbitrary_select_v2",
        "source",
        required_capabilities=("source.arbitrary_select_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=(
            "source.channel.arbitrary_selection",
            "source.channel.basic",
        ),
        restore_coverage="source-v2-arbitrary-selection",
        required_verified_fields=(
            "source.identity",
            "source.channel.arbitrary_selection",
            "source.channel.basic",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.arbitrary_selection",
            "source.channel.basic",
            "source.channel.output",
        ),
        postcondition_fields=(
            "source.channel.arbitrary_selection",
            "source.channel.basic",
            "source.channel.output",
        ),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "arbitrary_selection"),
    ),
    _spec(
        "source.arbitrary_volatile_replace_v2",
        "source",
        required_capabilities=("source.arbitrary_volatile_replace_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=(
            "source.channel.arbitrary_selection",
            "source.channel.arbitrary_storage",
            "source.channel.basic",
        ),
        restore_coverage="source-v2-arbitrary-volatile",
        required_verified_fields=(
            "source.identity",
            "source.channel.arbitrary_selection",
            "source.channel.basic",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.arbitrary_selection",
            "source.channel.basic",
            "source.channel.output",
        ),
        postcondition_fields=(
            "source.channel.arbitrary_selection",
            "source.channel.basic",
            "source.channel.output",
        ),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=(
            "source_v2",
            "output_must_be_off",
            "arbitrary_volatile_replace",
            "payload_not_artifact",
            "no_retry",
        ),
    ),
    _spec(
        "source.counter_configure_v2",
        "source",
        required_capabilities=("source.counter_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.input.counter",),
        restore_coverage="source-v2-counter-no-rollback",
        required_verified_fields=("source.identity", "source.input.counter"),
        verification_fields=("source.identity", "source.input.counter"),
        postcondition_fields=("source.input.counter",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "counter_input_configuration", "no_automatic_rollback"),
    ),
    _spec(
        "source.counter_enable_v2",
        "source",
        required_capabilities=("source.counter_enable_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.input.counter",),
        restore_coverage="source-v2-counter-no-rollback",
        required_verified_fields=("source.identity", "source.input.counter"),
        verification_fields=("source.identity", "source.input.counter"),
        postcondition_fields=("source.input.counter",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "counter_enable", "no_automatic_rollback"),
    ),
    _spec(
        "source.counter_disable_v2",
        "source",
        required_capabilities=("source.counter_enable_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.input.counter",),
        restore_coverage="source-v2-counter-no-rollback",
        required_verified_fields=("source.identity", "source.input.counter"),
        verification_fields=("source.identity", "source.input.counter"),
        postcondition_fields=("source.input.counter",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "counter_disable", "no_automatic_rollback"),
    ),
    _spec(
        "source.counter_measure_v2",
        "source",
        required_capabilities=("source.counter_measure_v2",),
        effect="stateful_read",
        lease_mode="exclusive",
        restore_coverage="none-read-only",
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("counter_measurement_query",),
    ),
    _spec(
        "source.combine_configure_v2",
        "source",
        required_capabilities=("source.combine_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.cross_channel.combine",),
        restore_coverage="source-v2-combine",
        required_verified_fields=(
            "source.identity",
            "source.channel.output",
            "source.cross_channel.combine",
            "source.cross_channel.relation_graph",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.output",
            "source.cross_channel.combine",
            "source.cross_channel.relation_graph",
        ),
        postcondition_fields=(
            "source.channel.output",
            "source.cross_channel.combine",
            "source.cross_channel.relation_graph",
        ),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "cross_channel_relation"),
    ),
    _spec(
        "source.coupling_configure_v2",
        "source",
        required_capabilities=("source.coupling_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.cross_channel.coupling",),
        restore_coverage="source-v2-coupling",
        required_verified_fields=(
            "source.identity",
            "source.channel.output",
            "source.cross_channel.coupling",
            "source.cross_channel.relation_graph",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.output",
            "source.cross_channel.coupling",
            "source.cross_channel.relation_graph",
        ),
        postcondition_fields=(
            "source.channel.output",
            "source.cross_channel.coupling",
            "source.cross_channel.relation_graph",
        ),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "cross_channel_relation"),
    ),
    _spec(
        "source.tracking_configure_v2",
        "source",
        required_capabilities=("source.tracking_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.cross_channel.tracking",),
        restore_coverage="source-v2-tracking",
        required_verified_fields=(
            "source.identity",
            "source.channel.output",
            "source.cross_channel.relation_graph",
            "source.cross_channel.tracking",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.output",
            "source.cross_channel.relation_graph",
            "source.cross_channel.tracking",
        ),
        postcondition_fields=(
            "source.channel.output",
            "source.cross_channel.relation_graph",
            "source.cross_channel.tracking",
        ),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "cross_channel_relation"),
    ),
    _spec(
        "source.phase_relation_configure_v2",
        "source",
        required_capabilities=("source.phase_relation_configure_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.cross_channel.phase_relation",),
        restore_coverage="source-v2-phase-relation",
        required_verified_fields=(
            "source.identity",
            "source.channel.output",
            "source.cross_channel.phase_relation",
            "source.cross_channel.relation_graph",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.output",
            "source.cross_channel.phase_relation",
            "source.cross_channel.relation_graph",
        ),
        postcondition_fields=(
            "source.channel.output",
            "source.cross_channel.phase_relation",
            "source.cross_channel.relation_graph",
        ),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "output_must_be_off", "cross_channel_relation"),
    ),
    _spec(
        "source.output_enable_v2",
        "source",
        required_capabilities=("source.output_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.output",),
        restore_coverage="source-v2-output",
        required_verified_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
        ),
        verification_fields=(
            "source.identity",
            "source.channel.basic",
            "source.channel.output",
        ),
        postcondition_fields=("source.channel.basic", "source.channel.output"),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "dangerous_output"),
    ),
    _spec(
        "source.output_disable_v2",
        "source",
        required_capabilities=("source.output_v2",),
        effect="write",
        lease_mode="exclusive",
        changed_fields=("source.channel.output",),
        restore_coverage="source-v2-output",
        required_verified_fields=("source.channel.output",),
        verification_fields=("source.channel.output",),
        postcondition_fields=("source.channel.output",),
        cleanup_verification_fields=("source.channel.output",),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=5_000,
        error_check_minimum="disabled",
        risk_flags=("source_v2", "safe_output_off"),
    ),
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
    _spec(
        "rf_source.idn",
        "rf_source",
        required_capabilities=("rf_source.idn",),
        effect="observe",
    ),
    _spec(
        "rf_source.snapshot",
        "rf_source",
        required_capabilities=("rf_source.snapshot",),
        effect="stateful_read",
        lease_mode="exclusive",
        restore_coverage="none-read-only",
        error_check_minimum="disabled",
        risk_flags=("state_dependent_query",),
    ),
    _spec(
        "rf_source.trigger_snapshot",
        "rf_source",
        required_capabilities=("rf_source.trigger_snapshot",),
        effect="stateful_read",
        lease_mode="exclusive",
        restore_coverage="none-read-only",
        error_check_minimum="disabled",
        risk_flags=("state_dependent_query", "trigger_configuration"),
    ),
    _spec(
        "rf_source.set_frequency",
        "rf_source",
        required_capabilities=("rf_source.snapshot", "rf_source.cw_configure"),
        effect="write",
        changed_fields=("rf_source.port.frequency_hz",),
        restore_coverage="none",
        risk_flags=("rf_output_must_be_off", "signal_level", "state_drift"),
        safe_alternatives=("rf_source.snapshot",),
    ),
    _spec(
        "rf_source.set_power_dbm",
        "rf_source",
        required_capabilities=("rf_source.snapshot", "rf_source.cw_configure"),
        effect="write",
        changed_fields=("rf_source.port.power_dbm",),
        restore_coverage="none",
        risk_flags=("rf_output_must_be_off", "signal_level", "state_drift"),
        safe_alternatives=("rf_source.snapshot",),
    ),
    _spec(
        "rf_source.modulation_configure",
        "rf_source",
        required_capabilities=("rf_source.snapshot", "rf_source.modulation_configure"),
        effect="write",
        changed_fields=(
            "rf_source.modulation.kind",
            "rf_source.modulation.source",
            "rf_source.modulation.waveform",
            "rf_source.modulation.value",
            "rf_source.modulation.internal_frequency_hz",
            "rf_source.modulation.enabled",
        ),
        restore_coverage="none",
        risk_flags=("rf_output_must_be_off", "modulation_state", "state_drift"),
        safe_alternatives=("rf_source.snapshot",),
    ),
    _spec(
        "rf_source.modulation_disable",
        "rf_source",
        required_capabilities=("rf_source.snapshot", "rf_source.modulation_disable"),
        effect="write",
        changed_fields=(
            "rf_source.modulation.enabled_modes",
            "rf_source.modulation.global_enabled",
        ),
        restore_coverage="none",
        risk_flags=(
            "rf_output_must_be_off",
            "modulation_state",
            "safe_modulation_disable",
            "state_drift",
        ),
        safe_alternatives=("rf_source.snapshot",),
    ),
    _spec(
        "rf_source.modulated_output_enable",
        "rf_source",
        required_capabilities=(
            "rf_source.snapshot",
            "rf_source.output",
            "rf_source.modulation_configure",
            "rf_source.modulated_output_enable",
        ),
        effect="write",
        changed_fields=("rf_source.port.output_enabled",),
        restore_coverage="none",
        risk_flags=(
            "dangerous_output",
            "rf_output_enable",
            "active_modulation",
            "state_drift",
        ),
        safe_alternatives=("rf_source.snapshot",),
    ),
    _spec(
        "rf_source.pulse_configure",
        "rf_source",
        required_capabilities=("rf_source.snapshot", "rf_source.pulse_configure"),
        effect="write",
        changed_fields=(
            "rf_source.pulse.source",
            "rf_source.pulse.mode",
            "rf_source.pulse.period_s",
            "rf_source.pulse.width_s",
            "rf_source.pulse.polarity",
            "rf_source.pulse.state",
        ),
        restore_coverage="none",
        risk_flags=("rf_output_must_be_off", "pulse_state", "state_drift"),
        safe_alternatives=("rf_source.snapshot",),
    ),
    _spec(
        "rf_source.pulse_output_enable",
        "rf_source",
        required_capabilities=(
            "rf_source.snapshot",
            "rf_source.pulse_configure",
            "rf_source.pulse_output",
        ),
        effect="write",
        changed_fields=("rf_source.physical_interface.pulse_output.enabled",),
        restore_coverage="none",
        risk_flags=(
            "rf_output_must_be_off",
            "physical_interface_output",
            "pulse_output_state",
            "state_drift",
        ),
        safe_alternatives=("rf_source.snapshot",),
    ),
    _spec(
        "rf_source.pulse_output_disable",
        "rf_source",
        required_capabilities=(
            "rf_source.snapshot",
            "rf_source.pulse_configure",
            "rf_source.pulse_output",
        ),
        effect="write",
        changed_fields=("rf_source.physical_interface.pulse_output.enabled",),
        restore_coverage="none",
        risk_flags=("physical_interface_output", "pulse_output_state", "state_drift"),
        safe_alternatives=("rf_source.snapshot",),
    ),
    _spec(
        "rf_source.sweep_configure",
        "rf_source",
        required_capabilities=("rf_source.snapshot", "rf_source.sweep_configure"),
        effect="write",
        changed_fields=(
            "rf_source.sweep.type",
            "rf_source.sweep.direction",
            "rf_source.sweep.shape",
            "rf_source.sweep.spacing",
            "rf_source.sweep.start_frequency_hz",
            "rf_source.sweep.stop_frequency_hz",
            "rf_source.sweep.points",
            "rf_source.sweep.dwell_s",
            "rf_source.sweep.state",
        ),
        restore_coverage="none",
        risk_flags=("rf_output_must_be_off", "sweep_disabled", "state_drift"),
        safe_alternatives=("rf_source.snapshot",),
    ),
    _spec(
        "rf_source.output_enable",
        "rf_source",
        required_capabilities=("rf_source.snapshot", "rf_source.output"),
        effect="write",
        changed_fields=("rf_source.port.output_enabled",),
        restore_coverage="none",
        risk_flags=("dangerous_output", "rf_output_enable", "state_drift"),
        safe_alternatives=("rf_source.snapshot",),
    ),
    _spec(
        "rf_source.output_disable",
        "rf_source",
        required_capabilities=("rf_source.snapshot", "rf_source.output"),
        effect="write",
        changed_fields=("rf_source.port.output_enabled",),
        restore_coverage="none",
        risk_flags=("safe_output_disable", "state_drift"),
        safe_alternatives=("rf_source.snapshot",),
    ),
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

_SCOPE_TRACE_TRANSFER_FIELDS = (
    "scope.run_state",
    "scope.waveform_source",
    "scope.waveform_mode",
    "scope.query_response_header",
    "scope.waveform_format",
    "scope.waveform_byte_order",
    "scope.waveform_points",
    "scope.waveform_transfer_window",
)


def _scope_operation(
    operation: str,
    *,
    required_capabilities: tuple[str, ...],
    effect: OperationEffect,
    timeout_ms: int,
    changed_fields: tuple[str, ...] = (),
    restore_coverage: str = "none",
    verification_fields: tuple[str, ...] = (),
    postcondition_fields: tuple[str, ...] = (),
    cleanup_verification_fields: tuple[str, ...] = (),
    risk_flags: tuple[str, ...] = (),
    binary_limits: tuple[int, int, int, int] | None = None,
    error_check_minimum: ErrorCheckMinimum | None = None,
) -> OperationSpec:
    binary = binary_limits or (None, None, None, None)
    return _spec(
        operation,
        "scope",
        required_capabilities=required_capabilities,
        optional_capabilities=(
            ("scope.error_drain_v1",) if error_check_minimum is not None else ()
        ),
        effect=effect,
        lease_mode="exclusive",
        changed_fields=changed_fields,
        restore_coverage=restore_coverage,
        required_verified_fields=("scope.identity",),
        verification_fields=verification_fields,
        postcondition_fields=postcondition_fields,
        cleanup_verification_fields=cleanup_verification_fields,
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=timeout_ms,
        binary_response_max_bytes=binary[0],
        binary_operation_max_bytes=binary[1],
        binary_query_max_count=binary[2],
        binary_resynchronization_max_bytes=binary[3],
        error_check_minimum=error_check_minimum,
        risk_flags=risk_flags,
    )


_SCOPE_EXTENSION_SPECS = (
    _scope_operation(
        "scope.screenshot_profile",
        required_capabilities=("scope.screenshot_profile",),
        effect="stateful_read",
        timeout_ms=SCOPE_PROFILE_OPERATION_TIMEOUT_MS,
        risk_flags=("profile_query",),
    ),
    _scope_operation(
        "scope.screenshot_v2",
        required_capabilities=("scope.screenshot_v2",),
        effect="write",
        timeout_ms=SCOPE_SCREENSHOT_OPERATION_TIMEOUT_MS,
        changed_fields=(
            "scope.display_menu",
            "scope.display_color",
            "scope.error_queue",
            "output.screenshot",
        ),
        restore_coverage="screenshot-baseline-only",
        verification_fields=("scope.display_menu", "scope.display_color"),
        cleanup_verification_fields=("scope.display_menu", "scope.display_color"),
        risk_flags=("front_panel_state", "binary_response", "temporary_display_setup"),
        binary_limits=(
            SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES,
            SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES,
            SCOPE_SCREENSHOT_BINARY_QUERY_MAX_COUNT,
            SCOPE_SCREENSHOT_BINARY_RESYNCHRONIZATION_MAX_BYTES,
        ),
        error_check_minimum="disabled",
    ),
    _scope_operation(
        "scope.acquisition_run_state",
        required_capabilities=("scope.acquisition_run_state",),
        effect="stateful_read",
        timeout_ms=SCOPE_PROFILE_OPERATION_TIMEOUT_MS,
        risk_flags=("state_observation",),
    ),
    _scope_operation(
        "scope.acquisition_start",
        required_capabilities=("scope.acquisition_control", "scope.acquisition_run_state"),
        effect="write",
        timeout_ms=SCOPE_ACQUISITION_OPERATION_TIMEOUT_MS,
        changed_fields=(
            "scope.run_state",
            "scope.trigger",
            "scope.acquisition",
            "scope.error_queue",
        ),
        restore_coverage="failure-cleanup-only",
        verification_fields=("scope.trigger", "scope.acquisition"),
        postcondition_fields=("scope.run_state", "scope.trigger", "scope.acquisition"),
        cleanup_verification_fields=(
            "scope.run_state",
            "scope.trigger",
            "scope.acquisition",
        ),
        risk_flags=("trigger", "acquisition_state", "recovery_required"),
        error_check_minimum="disabled",
    ),
    _scope_operation(
        "scope.acquisition_single",
        required_capabilities=("scope.acquisition_control", "scope.acquisition_run_state"),
        effect="acquire",
        timeout_ms=SCOPE_ACQUISITION_OPERATION_TIMEOUT_MS,
        changed_fields=(
            "scope.run_state",
            "scope.trigger",
            "scope.acquisition",
            "scope.error_queue",
        ),
        restore_coverage="failure-cleanup-only",
        verification_fields=("scope.trigger", "scope.acquisition"),
        postcondition_fields=("scope.run_state", "scope.trigger", "scope.acquisition"),
        cleanup_verification_fields=(
            "scope.run_state",
            "scope.trigger",
            "scope.acquisition",
        ),
        risk_flags=("trigger", "acquisition_state", "recovery_required"),
        error_check_minimum="disabled",
    ),
    _scope_operation(
        "scope.acquisition_stop",
        required_capabilities=("scope.acquisition_control", "scope.acquisition_run_state"),
        effect="write",
        timeout_ms=SCOPE_PROFILE_OPERATION_TIMEOUT_MS,
        changed_fields=("scope.run_state", "scope.error_queue"),
        restore_coverage="failure-cleanup-only",
        postcondition_fields=("scope.run_state",),
        cleanup_verification_fields=("scope.run_state",),
        risk_flags=("acquisition_state", "recovery_required"),
        error_check_minimum="disabled",
    ),
    _scope_operation(
        "scope.trace_metadata",
        required_capabilities=("scope.trace_metadata",),
        effect="stateful_read",
        timeout_ms=SCOPE_PROFILE_OPERATION_TIMEOUT_MS,
        risk_flags=("analysis_state",),
        error_check_minimum="disabled",
    ),
    _scope_operation(
        "scope.fetch_trace",
        required_capabilities=("scope.fetch_trace",),
        effect="acquire",
        timeout_ms=SCOPE_TRACE_OPERATION_TIMEOUT_MS,
        changed_fields=(*_SCOPE_TRACE_TRANSFER_FIELDS, "scope.error_queue", "output.trace"),
        restore_coverage="trace-baseline-only",
        verification_fields=_SCOPE_TRACE_TRANSFER_FIELDS,
        cleanup_verification_fields=_SCOPE_TRACE_TRANSFER_FIELDS,
        risk_flags=("acquisition_state", "temporary_transfer_setup", "binary_response"),
        binary_limits=(
            SCOPE_TRACE_BINARY_RESPONSE_MAX_BYTES,
            SCOPE_TRACE_BINARY_OPERATION_MAX_BYTES,
            SCOPE_TRACE_BINARY_QUERY_MAX_COUNT,
            SCOPE_TRACE_BINARY_RESYNCHRONIZATION_MAX_BYTES,
        ),
        error_check_minimum="disabled",
    ),
)

SCOPE_OPERATION_SPECS: Mapping[str, OperationSpec] = MappingProxyType(
    {spec.operation: spec for spec in _SCOPE_EXTENSION_SPECS}
)

_SCOPE_PORTABILITY_V2_SPECS = (
    _spec(
        "scope.snapshot_v2",
        "scope",
        required_capabilities=("scope.snapshot_v2",),
        effect="stateful_read",
        lease_mode="exclusive",
        restore_coverage="none-read-only",
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=SCOPE_SNAPSHOT_V2_OPERATION_TIMEOUT_MS,
        error_check_minimum="disabled",
        risk_flags=("profile_query",),
    ),
    _spec(
        "scope.acquisition_status_v2",
        "scope",
        required_capabilities=("scope.acquisition_status_v2",),
        effect="stateful_read",
        lease_mode="exclusive",
        restore_coverage="none-read-only",
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=SCOPE_ACQUISITION_STATUS_V2_OPERATION_TIMEOUT_MS,
        error_check_minimum="disabled",
        risk_flags=("profile_query",),
    ),
    _scope_operation(
        "scope.capture_average_v2",
        required_capabilities=("scope.capture_average_v2",),
        effect="acquire",
        timeout_ms=SCOPE_AVERAGE_CAPTURE_V2_OPERATION_TIMEOUT_MS,
        changed_fields=_SCOPE_AVERAGE_CAPTURE_CHANGED_FIELDS,
        restore_coverage="average-capture-baseline",
        verification_fields=(
            "scope.identity",
            *_SCOPE_AVERAGE_CAPTURE_CHANGED_FIELDS,
        ),
        postcondition_fields=_SCOPE_AVERAGE_CAPTURE_GRANULAR_FIELDS,
        cleanup_verification_fields=_SCOPE_AVERAGE_CAPTURE_RESTORE_FIELDS,
        risk_flags=(
            "trigger",
            "acquisition_state",
            "temporary_transfer_setup",
            "binary_response",
            "recovery_required",
        ),
        binary_limits=(
            SCOPE_AVERAGE_CAPTURE_V2_BINARY_RESPONSE_MAX_BYTES,
            SCOPE_AVERAGE_CAPTURE_V2_BINARY_OPERATION_MAX_BYTES,
            SCOPE_AVERAGE_CAPTURE_V2_BINARY_QUERY_MAX_COUNT,
            SCOPE_AVERAGE_CAPTURE_V2_BINARY_RESYNCHRONIZATION_MAX_BYTES,
        ),
        error_check_minimum="disabled",
    ),
    _spec(
        "scope.measurement_statistics_v2",
        "scope",
        required_capabilities=("scope.measurement_statistics_v2",),
        effect="stateful_read",
        lease_mode="exclusive",
        restore_coverage="none-read-only",
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=SCOPE_MEASUREMENT_STATISTICS_V2_OPERATION_TIMEOUT_MS,
        error_check_minimum="disabled",
        risk_flags=("profile_query",),
    ),
    _spec(
        "scope.fft_status_v2",
        "scope",
        required_capabilities=("scope.fft_status_v2",),
        effect="stateful_read",
        lease_mode="exclusive",
        restore_coverage="none-read-only",
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=SCOPE_FFT_STATUS_V2_OPERATION_TIMEOUT_MS,
        error_check_minimum="disabled",
        risk_flags=("profile_query",),
    ),
    _spec(
        "scope.cursor_readout_v2",
        "scope",
        required_capabilities=("scope.cursor_readout_v2",),
        effect="stateful_read",
        lease_mode="exclusive",
        restore_coverage="none-read-only",
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=SCOPE_CURSOR_READOUT_V2_OPERATION_TIMEOUT_MS,
        error_check_minimum="disabled",
        risk_flags=("profile_query",),
    ),
)

SCOPE_PORTABILITY_V2_OPERATION_SPECS: Mapping[str, OperationSpec] = MappingProxyType(
    {spec.operation: spec for spec in _SCOPE_PORTABILITY_V2_SPECS}
)

OPERATION_REGISTRY = OperationRegistry(
    {
        **{spec.operation: spec for spec in _BUILTIN_SPECS},
        **SCOPE_OPERATION_SPECS,
        **SCOPE_PORTABILITY_V2_OPERATION_SPECS,
    }
)


def get_operation_spec(operation: str) -> OperationSpec | None:
    """Return metadata for an operation, or ``None`` when it is not registered."""

    return OPERATION_REGISTRY.get(operation)


def require_operation_spec(operation: str) -> OperationSpec:
    return OPERATION_REGISTRY.require(operation)


def list_operation_specs(*, instrument_kind: str | None = None) -> tuple[OperationSpec, ...]:
    return OPERATION_REGISTRY.all(instrument_kind=instrument_kind)
