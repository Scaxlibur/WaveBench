"""Frozen inventory of every currently supported Source V1 write route.

This is deliberately an internal migration ledger, not a new V1 API.  There
is no Source V2 write capability in this core revision, so a record does not
claim a V2 mapping.  A feature-specific V2 write implementation must classify
each related route as synonymous, overlapping, or disjoint before it can be
registered.
"""

from __future__ import annotations

from dataclasses import dataclass

from wavebench.instruments.source_extensions import SourceV1WriteRouteId


@dataclass(frozen=True, slots=True)
class SourceV1WriteRoute:
    """One Service write route and its non-Service public entry points."""

    route: SourceV1WriteRouteId
    operation: str | None
    entrypoints: tuple[str, ...]
    may_change_signal_when_output_on: bool
    may_start_or_reenergize_output: bool
    mutates_storage: bool

    def __post_init__(self) -> None:
        if self.operation is not None and (
            not self.operation.startswith("source.") or self.operation.strip() != self.operation
        ):
            raise ValueError("Source V1 write route operation must be a trimmed source.* ID")
        if not self.entrypoints:
            raise ValueError("Source V1 write route must declare an entry point")
        if tuple(sorted(self.entrypoints)) != self.entrypoints or len(set(self.entrypoints)) != len(
            self.entrypoints
        ):
            raise ValueError("Source V1 write route entry points must be sorted and unique")
        if any(not entrypoint or entrypoint.strip() != entrypoint for entrypoint in self.entrypoints):
            raise ValueError("Source V1 write route entry points must be non-empty and trimmed")
        if self.mutates_storage and not self.may_change_signal_when_output_on:
            raise ValueError("Source V1 storage mutation must declare its signal-state side effect")


# Route order is the public SourceV1WriteRouteId enum order.  The direct
# SourceService method is encoded in ``route.value``; entrypoints below only
# list additional CLI, run-plan, TUI, sweep, and restore paths.
SOURCE_V1_WRITE_ROUTE_INVENTORY: tuple[SourceV1WriteRoute, ...] = (
    SourceV1WriteRoute(
        SourceV1WriteRouteId.SET_FREQUENCY,
        "source.set_frequency",
        ("cli.source.set-freq", "run-plan.source.set_freq", "sweep.discrete", "tui.source.set_frequency"),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.SET_FUNCTION,
        "source.set_function",
        ("cli.source.set-func", "run-plan.source.set_func", "sweep.discrete", "tui.source.set_function"),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.SET_AMPLITUDE_VPP,
        "source.set_amplitude_vpp",
        ("cli.source.set-vpp", "run-plan.source.set_vpp", "sweep.discrete", "tui.source.set_amplitude_vpp"),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.SET_SQUARE_DUTY_CYCLE,
        "source.set_square_duty_cycle",
        ("cli.source.set-duty", "run-plan.source.set_duty"),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.SET_OUTPUT,
        "source.output",
        ("cli.source.output", "run-plan.source.output", "run.safety-gate", "tui.source.set_output"),
        True,
        True,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.CONFIGURE_COUPLING,
        "source.coupling_configure",
        ("python.source-service",),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.CONFIGURE_HARMONICS,
        "source.harmonic_configure",
        ("python.source-service",),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.CONFIGURE_AM,
        "source.modulation_am_configure",
        ("python.source-service",),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.CONFIGURE_FM,
        "source.modulation_fm_configure",
        ("python.source-service",),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.CONFIGURE_PM,
        "source.modulation_pm_configure",
        ("python.source-service",),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.CONFIGURE_PWM,
        "source.modulation_pwm_configure",
        ("python.source-service",),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.CONFIGURE_PULSE,
        "source.pulse_configure",
        ("python.source-service",),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.CONFIGURE_BURST,
        "source.burst_configure",
        ("python.source-service",),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.TRIGGER_BURST,
        "source.burst_trigger",
        ("python.source-service",),
        True,
        True,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.CONFIGURE_SWEEP,
        "source.sweep_configure",
        ("python.source-service",),
        True,
        False,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.TRIGGER_SWEEP,
        "source.sweep_trigger",
        ("python.source-service",),
        True,
        True,
        False,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.UPLOAD_ARBITRARY,
        "source.arbitrary_upload",
        ("cli.source.arb-load", "run-plan.source.arb_load"),
        True,
        True,
        True,
    ),
    SourceV1WriteRoute(
        SourceV1WriteRouteId.RESTORE,
        None,
        ("run.restore-source-state", "sweep.discrete.restore-source-state"),
        True,
        True,
        False,
    ),
)


__all__ = ["SOURCE_V1_WRITE_ROUTE_INVENTORY", "SourceV1WriteRoute"]
