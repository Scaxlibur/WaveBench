from __future__ import annotations

from typing import Protocol

from wavebench.config import SafetyLimitsConfig
from wavebench.errors import ConfigError
from wavebench.services.run_plan import RunPlan


class ScopeSafetyService(Protocol):
    def channel_coupling(self, channel: int) -> str: ...

    def require_high_impedance(self, channel: int, *, allow_50ohm: bool = False) -> str: ...


EXECUTABLE_STEP_KINDS = {
    "power.status",
    "power.set",
    "power.output",
    "scope.auto",
    "scope.capture",
    "sweep.frequency_response",
    "source.status",
    "source.set_freq",
    "source.arb_load",
    "source.set_func",
    "source.set_vpp",
    "source.set_duty",
    "source.output",
    "source.basic_configure_v2",
    "source.output_enable_v2",
    "source.output_disable_v2",
    "source.harmonics_configure_v2",
    "source.modulation_configure_v2",
    "source.modulation_pm_configure_v2",
    "source.pulse_configure_v2",
    "dmm.read",
    "sleep",
}


def check_run_plan_safety_limits(plan: RunPlan, limits: SafetyLimitsConfig) -> None:
    for step in plan.steps:
        if step.kind == "source.set_vpp":
            _check_limit(
                step.fields["value_vpp"],
                limits.max_source_vpp,
                field=f"run step {step.index} source amplitude / 运行步骤 {step.index} 信号源幅度",
                config_key="max_source_vpp",
                unit="Vpp",
            )
        elif step.kind == "source.basic_configure_v2" and "amplitude_vpp" in step.fields:
            _check_limit(
                step.fields["amplitude_vpp"],
                limits.max_source_vpp,
                field=(
                    f"run step {step.index} Source V2 amplitude / "
                    f"运行步骤 {step.index} Source V2 幅度"
                ),
                config_key="max_source_vpp",
                unit="Vpp",
            )
        elif step.kind == "sweep.frequency_response":
            for amplitude in step.fields.get("amplitudes_vpp", []):
                _check_limit(
                    amplitude,
                    limits.max_source_vpp,
                    field=(
                        f"run step {step.index} frequency-response amplitude / "
                        f"运行步骤 {step.index} 频响信号源幅度"
                    ),
                    config_key="max_source_vpp",
                    unit="Vpp",
                )
        elif step.kind == "source.arb_load":
            _check_limit(
                step.fields["amplitude_vpp"],
                limits.max_source_vpp,
                field=f"run step {step.index} arbitrary waveform amplitude / 运行步骤 {step.index} 任意波幅度",
                config_key="max_source_vpp",
                unit="Vpp",
            )
        elif step.kind == "power.set":
            _check_limit(
                step.fields["voltage_v"],
                limits.max_power_voltage_v,
                field=f"run step {step.index} power voltage / 运行步骤 {step.index} 电源电压",
                config_key="max_power_voltage_v",
                unit="V",
            )
            _check_limit(
                step.fields["current_limit_a"],
                limits.max_power_current_limit_a,
                field=f"run step {step.index} power current limit / 运行步骤 {step.index} 电源限流",
                config_key="max_power_current_limit_a",
                unit="A",
            )


def plan_scope_guard_channels(plan: RunPlan, default_channel: int) -> list[int]:
    channels: list[int] = []
    for step in plan.steps:
        if step.kind == "scope.capture":
            channel = step.fields.get("channel") or default_channel
            if channel not in channels:
                channels.append(channel)
        elif step.kind == "sweep.frequency_response":
            for field in ("reference_channel", "response_channel"):
                channel = step.fields[field]
                if channel not in channels:
                    channels.append(channel)
    return channels


def run_scope_safety_guards(
    plan: RunPlan,
    *,
    scope_service: ScopeSafetyService,
    default_channel: int,
) -> None:
    if plan.safety.require_scope_coupling_not:
        if plan.safety.scope_guard_channel is None:  # pragma: no cover - parser enforces this
            raise ConfigError("safety.scope_guard_channel is required")
        channel = plan.safety.scope_guard_channel
        coupling = scope_service.channel_coupling(channel)
        blocked = set(plan.safety.require_scope_coupling_not)
        if coupling.strip().upper() in blocked:
            blocked_text = ", ".join(sorted(blocked))
            raise ConfigError(
                f"safety guard failed: scope CH{channel} coupling is {coupling}; "
                f"blocked coupling value(s): {blocked_text}"
            )
    for channel in plan_scope_guard_channels(plan, default_channel):
        scope_service.require_high_impedance(channel, allow_50ohm=plan.safety.allow_50ohm)


def reject_unsupported_steps(plan: RunPlan) -> None:
    unsupported = [step.kind for step in plan.steps if step.kind not in EXECUTABLE_STEP_KINDS]
    if unsupported:
        raise ConfigError(
            "run plan execution does not support step kind(s) yet: " + ", ".join(unsupported)
        )


def _check_limit(
    value: float | None, limit: float | None, *, field: str, config_key: str, unit: str
) -> None:
    if value is None or limit is None:
        return
    if value > limit:
        raise ConfigError(
            f"safety limit exceeded / 安全上限已超出: {field} {value:.12g} {unit} "
            f"> {config_key} {limit:.12g} {unit}"
        )
