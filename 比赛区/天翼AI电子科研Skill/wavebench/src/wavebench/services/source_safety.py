"""Core-only Source V2 safety configuration primitives.

These functions intentionally do not open a session or authorize a write.  They
only turn explicit experiment-table limits into the prerequisite used by a
future energy-increasing Source V2 operation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from math import isfinite
import re

from wavebench.config import SafetyLimitsConfig, WaveBenchConfig
from wavebench.errors import ConfigError, SourceSafetyLimitsRequiredError
from wavebench.instruments.source_extensions import (
    SourceScopeRef,
    SourceTerminationEvidence,
    TerminationEvidenceLifetime,
    TerminationEvidenceSource,
    TerminationKind,
    TerminationSpec,
    ResistanceBounds,
    source_v2_digest,
)
from wavebench.services.resource_lease import resource_fingerprint


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


@dataclass(frozen=True, slots=True)
class SourceEnergySafetyLimits:
    """Three independent, explicit safety axes for Source V2 energy operations."""

    max_source_vpp: float
    min_source_port_voltage_v: float
    max_source_port_voltage_v: float

    def __post_init__(self) -> None:
        for name, value in (
            ("max_source_vpp", self.max_source_vpp),
            ("min_source_port_voltage_v", self.min_source_port_voltage_v),
            ("max_source_port_voltage_v", self.max_source_port_voltage_v),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise ConfigError(f"source safety limit {name} must be a finite number")
        if self.max_source_vpp <= 0:
            raise ConfigError("source safety limit max_source_vpp must be > 0")
        if self.min_source_port_voltage_v >= self.max_source_port_voltage_v:
            raise ConfigError(
                "source safety limit min_source_port_voltage_v must be < "
                "max_source_port_voltage_v"
            )


def require_source_v2_energy_safety_limits(
    limits: SafetyLimitsConfig,
) -> SourceEnergySafetyLimits:
    """Return explicit V2 energy limits or fail closed before any instrument I/O."""

    required = (
        "max_source_vpp",
        "min_source_port_voltage_v",
        "max_source_port_voltage_v",
    )
    missing = tuple(name for name in required if getattr(limits, name, None) is None)
    if missing:
        raise SourceSafetyLimitsRequiredError(missing)
    return SourceEnergySafetyLimits(
        max_source_vpp=limits.max_source_vpp,  # type: ignore[arg-type]
        min_source_port_voltage_v=limits.min_source_port_voltage_v,  # type: ignore[arg-type]
        max_source_port_voltage_v=limits.max_source_port_voltage_v,  # type: ignore[arg-type]
    )


class SourceTerminationEvidenceStatus(StrEnum):
    VALID = "valid"
    TARGET_MISMATCH = "target_mismatch"
    RESOURCE_MISMATCH = "resource_mismatch"
    BINDING_MISMATCH = "binding_mismatch"
    EXPIRED = "expired"
    OBSERVED_IN_FUTURE = "observed_in_future"
    RUN_INTENT_REQUIRED = "run_intent_required"


@dataclass(frozen=True, slots=True)
class SourceTerminationEvidenceContext:
    """Core-owned binding facts for deciding whether termination evidence is usable."""

    target: SourceScopeRef
    resource_fingerprint: str
    config_digest: str
    correlation_id: str
    observed_at_utc: str
    run_intent_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, SourceScopeRef):
            raise ValueError("termination evidence context target has an invalid type")
        if self.target.scope.value != "channel":
            raise ValueError("termination evidence context target must be a channel scope")
        for label, value in (
            ("resource_fingerprint", self.resource_fingerprint),
            ("config_digest", self.config_digest),
        ):
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise ValueError(f"termination evidence context {label} has an invalid format")
        if not isinstance(self.correlation_id, str) or _SAFE_TOKEN.fullmatch(self.correlation_id) is None:
            raise ValueError("termination evidence context correlation_id has an invalid format")
        _parse_utc(self.observed_at_utc, "termination evidence context observed_at_utc")
        if self.run_intent_digest is not None and (
            not isinstance(self.run_intent_digest, str)
            or _SAFE_TOKEN.fullmatch(self.run_intent_digest) is None
        ):
            raise ValueError("termination evidence context run_intent_digest has an invalid format")


@dataclass(frozen=True, slots=True)
class SourceTerminationEvidenceValidation:
    status: SourceTerminationEvidenceStatus
    evidence: SourceTerminationEvidence | None = None

    @property
    def is_valid(self) -> bool:
        return self.status is SourceTerminationEvidenceStatus.VALID


def source_safety_config_digest(config: WaveBenchConfig) -> str:
    """Hash only the source-side safety semantics without exposing the resource string."""

    source = config.source
    resource = None if source is None or source.resource is None else _resource_digest(source.resource)
    return source_v2_digest(
        {
            "schema": "wavebench.source.safety.config.v1",
            "source": (
                None
                if source is None
                else {
                    "driver": source.driver,
                    "resource_fingerprint": resource,
                    "default_channel": source.default_channel,
                    "terminations": tuple(asdict(item) for item in source.terminations),
                }
            ),
            "safety_limits": asdict(config.safety_limits),
        }
    )


def source_termination_evidence_context(
    config: WaveBenchConfig,
    *,
    target: SourceScopeRef,
    correlation_id: str,
    observed_at_utc: str | None = None,
    run_intent_digest: str | None = None,
) -> SourceTerminationEvidenceContext:
    """Create a current operation/run context without performing instrument I/O."""

    source = config.source
    if source is None or not source.resource:
        raise ConfigError("source termination evidence requires a configured source resource")
    return SourceTerminationEvidenceContext(
        target=target,
        resource_fingerprint=_resource_digest(source.resource),
        config_digest=source_safety_config_digest(config),
        correlation_id=correlation_id,
        observed_at_utc=observed_at_utc or _timestamp_utc(),
        run_intent_digest=run_intent_digest,
    )


def source_config_termination_evidence(
    config: WaveBenchConfig,
    *,
    context: SourceTerminationEvidenceContext,
) -> SourceTerminationEvidence | None:
    """Materialize a channel's static config evidence for one bound operation context."""

    source = config.source
    if source is None:
        return None
    channel = context.target.channel
    assert channel is not None
    configured = next((item for item in source.terminations if item.channel == channel), None)
    if configured is None:
        return None
    bounds = (
        None
        if configured.minimum_ohm is None
        else ResistanceBounds(configured.minimum_ohm, configured.maximum_ohm)
    )
    termination = TerminationSpec(TerminationKind(configured.kind), bounds)
    source_kind = TerminationEvidenceSource.CONFIG
    lifetime = TerminationEvidenceLifetime.CONFIG_DIGEST
    return SourceTerminationEvidence(
        target=context.target,
        termination=termination,
        source=source_kind,
        lifetime=lifetime,
        resource_fingerprint=context.resource_fingerprint,
        binding_digest=source_termination_binding_digest(
            context,
            source=source_kind,
            lifetime=lifetime,
        ),
        observed_at_utc=context.observed_at_utc,
        expires_at_utc=None,
        evidence_ref=f"config.source_terminations.ch{channel}",
    )


def source_termination_binding_digest(
    context: SourceTerminationEvidenceContext,
    *,
    source: TerminationEvidenceSource,
    lifetime: TerminationEvidenceLifetime,
) -> str:
    """Build the unforgeable-by-accident digest that binds evidence to its use."""

    if not isinstance(context, SourceTerminationEvidenceContext):
        raise TypeError("termination evidence context has an invalid type")
    if not isinstance(source, TerminationEvidenceSource) or not isinstance(
        lifetime,
        TerminationEvidenceLifetime,
    ):
        raise TypeError("termination evidence source/lifetime has an invalid type")
    if lifetime is TerminationEvidenceLifetime.RUN and context.run_intent_digest is None:
        raise ConfigError("run-lifetime termination evidence requires a run intent digest")
    return source_v2_digest(
        {
            "schema": "wavebench.source.termination.binding.v1",
            "target": context.target,
            "resource_fingerprint": context.resource_fingerprint,
            "config_digest": context.config_digest,
            "correlation_id": context.correlation_id,
            "run_intent_digest": (
                context.run_intent_digest
                if lifetime is TerminationEvidenceLifetime.RUN
                else None
            ),
            "source": source,
            "lifetime": lifetime,
        }
    )


def validate_source_termination_evidence(
    evidence: SourceTerminationEvidence,
    *,
    context: SourceTerminationEvidenceContext,
) -> SourceTerminationEvidenceValidation:
    """Fail closed when any target, binding, resource, or time fact differs."""

    if not isinstance(evidence, SourceTerminationEvidence):
        raise TypeError("termination evidence has an invalid type")
    if not isinstance(context, SourceTerminationEvidenceContext):
        raise TypeError("termination evidence context has an invalid type")
    if evidence.target != context.target:
        return SourceTerminationEvidenceValidation(SourceTerminationEvidenceStatus.TARGET_MISMATCH)
    if evidence.resource_fingerprint != context.resource_fingerprint:
        return SourceTerminationEvidenceValidation(SourceTerminationEvidenceStatus.RESOURCE_MISMATCH)
    if (
        evidence.lifetime is TerminationEvidenceLifetime.RUN
        and context.run_intent_digest is None
    ):
        return SourceTerminationEvidenceValidation(SourceTerminationEvidenceStatus.RUN_INTENT_REQUIRED)
    expected = source_termination_binding_digest(
        context,
        source=evidence.source,
        lifetime=evidence.lifetime,
    )
    if evidence.binding_digest != expected:
        return SourceTerminationEvidenceValidation(SourceTerminationEvidenceStatus.BINDING_MISMATCH)
    now = _parse_utc(context.observed_at_utc, "termination evidence context observed_at_utc")
    observed = _parse_utc(evidence.observed_at_utc, "termination evidence observed_at_utc")
    if observed > now:
        return SourceTerminationEvidenceValidation(SourceTerminationEvidenceStatus.OBSERVED_IN_FUTURE)
    if evidence.expires_at_utc is not None and _parse_utc(
        evidence.expires_at_utc,
        "termination evidence expires_at_utc",
    ) < now:
        return SourceTerminationEvidenceValidation(SourceTerminationEvidenceStatus.EXPIRED)
    return SourceTerminationEvidenceValidation(
        SourceTerminationEvidenceStatus.VALID,
        evidence=evidence,
    )


def _resource_digest(resource: str) -> str:
    return "sha256:" + resource_fingerprint(resource)


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be an RFC 3339 UTC timestamp") from exc


__all__ = [
    "SourceEnergySafetyLimits",
    "SourceTerminationEvidenceContext",
    "SourceTerminationEvidenceStatus",
    "SourceTerminationEvidenceValidation",
    "require_source_v2_energy_safety_limits",
    "source_config_termination_evidence",
    "source_safety_config_digest",
    "source_termination_binding_digest",
    "source_termination_evidence_context",
    "validate_source_termination_evidence",
]
