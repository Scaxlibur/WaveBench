from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import difflib
from math import log10
from typing import Any
import tomllib

from wavebench.config import normalize_waveform_points
from wavebench.errors import ConfigError
from wavebench.services.frequency_response import FIT_METHODS
from wavebench.services.frequency_response_adaptive import normalize_frequency_response_adaptive
from wavebench.services.frequency_response_baseline import normalize_frequency_response_baseline
from wavebench.services.frequency_response_calibration import normalize_frequency_response_calibration


ALLOWED_STEP_KINDS = {
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
    "power.status",
    "power.set",
    "power.output",
    "dmm.read",
    "sleep",
}

_REQUIRED_FIELDS = {
    "power.set": ("voltage_v", "current_limit_a"),
    "power.output": ("state",),
    "source.set_freq": ("frequency_hz",),
    "source.arb_load": ("file", "frequency_hz", "amplitude_vpp"),
    "source.set_func": ("function",),
    "source.set_vpp": ("value_vpp",),
    "source.set_duty": ("duty_percent",),
    "source.output": ("state",),
    "sweep.frequency_response": ("reference_channel", "response_channel"),
    "sleep": ("duration_s",),
}

_OPTIONAL_FIELDS = {
    "scope.auto": {"on_failure"},
    "scope.capture": {
        "channel",
        "label",
        "points",
        "time_range_s",
        "expect_frequency_hz",
        "window_frequency_hz",
        "target_cycles",
        "frequency_tolerance",
        "vertical_scale_v_per_div",
        "target_vpp",
        "save_csv",
        "save_npy",
        "screenshot",
        "quality_gate",
        "auto_recover",
        "autoscale_before_capture",
        "autoscale_settle_s",
        "expect",
        "expect_fft",
        "on_failure",
    },
    "sweep.frequency_response": {
        "label",
        "source_channel",
        "frequencies_hz",
        "start_frequency_hz",
        "stop_frequency_hz",
        "frequency_count",
        "spacing",
        "target_cycles",
        "settle_s",
        "frequency_tolerance",
        "min_signal_vpp",
        "points",
        "save_csv",
        "screenshot",
        "fit",
        "amplitudes_vpp",
        "start_vpp",
        "stop_vpp",
        "vpp_step",
        "autoscale_each_amplitude",
        "retry_warning_with_autoscale",
        "calibration",
        "baseline",
        "adaptive",
        "stop_conditions",
        "resume_from",
        "on_failure",
    },
    "source.status": {"channel", "on_failure"},
    "source.set_freq": {"channel", "on_failure"},
    "source.arb_load": {"channel", "offset_v", "sample_rate_hz", "max_points", "byte_order", "output_on", "on_failure"},
    "source.set_func": {"channel", "on_failure"},
    "source.set_vpp": {"channel", "on_failure"},
    "source.set_duty": {"channel", "on_failure"},
    "source.output": {"channel", "on_failure"},
    "power.status": {"channel", "on_failure"},
    "power.set": {"channel", "on_failure"},
    "power.output": {"channel", "on_failure"},
    "dmm.read": {"function", "expect", "on_failure"},
    "sleep": {"on_failure"},
}


_STEP_NOTES = {
    "scope.auto": "Explicit RTM2032 AUToscale. It changes front-panel settings and is never inserted implicitly.",
    "scope.capture": "Trigger one acquisition, write a capture package, and optionally evaluate quality/expect checks. Use target_vpp or vertical_scale_v_per_div to fit the waveform vertically before capture.",
    "sweep.frequency_response": "Sweep a source through discrete frequencies, capture reference and response channels in one acquisition per point, and write a Bode response CSV.",
    "source.status": "Read signal-generator channel state without changing output.",
    "source.arb_load": "Upload a DG4202 arbitrary waveform from CSV/NPY using DATA:DAC VOLATILE; output remains unchanged unless output_on = true.",
    "source.set_freq": "Set fixed source frequency in Hz; config may force FIX mode first.",
    "source.set_func": "Set source waveform function, for example SIN or SQU.",
    "source.set_vpp": "Set source amplitude in Vpp.",
    "source.set_duty": "Set square-wave duty cycle in percent; valid range is 0 < duty_percent < 100.",
    "source.output": "Turn source channel output on or off.",
    "power.status": "Read power-supply channel state without changing output.",
    "power.set": "Set DP800 voltage/current limit; does not change output state.",
    "power.output": "Turn power-supply channel output on or off; does not change voltage/current limit.",
    "dmm.read": "Read one DMM measurement over the configured backend; default function is dcv unless overridden.",
    "sleep": "Wait between hardware actions.",
}


@dataclass(frozen=True)
class StepSchema:
    kind: str
    required: tuple[str, ...]
    optional: frozenset[str]
    notes: str = ""


STEP_SCHEMAS = {
    kind: StepSchema(
        kind=kind,
        required=_REQUIRED_FIELDS.get(kind, ()),
        optional=frozenset(_OPTIONAL_FIELDS.get(kind, set())),
        notes=_STEP_NOTES.get(kind, ""),
    )
    for kind in sorted(ALLOWED_STEP_KINDS)
}


def run_plan_schema_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for schema in STEP_SCHEMAS.values():
        required = ", ".join(schema.required) or "-"
        optional = ", ".join(sorted(schema.optional)) or "-"
        rows.append({
            "kind": schema.kind,
            "required": required,
            "optional": optional,
            "notes": schema.notes,
        })
    return rows


def format_run_plan_schema() -> str:
    lines = [
        "WaveBench run plan schema",
        "",
        "Top-level tables:",
        "  [experiment] optional: name, label",
        "  [safety] optional: scope_guard_channel, require_scope_coupling_not, allow_50ohm",
        "  [restore] optional: source_state, source_channel, source_channels",
        "  [[steps]] required: kind",
        "",
        "Supported step kinds:",
    ]
    for row in run_plan_schema_rows():
        lines.append(f"  - {row['kind']}")
        lines.append(f"      required: {row['required']}")
        lines.append(f"      optional : {row['optional']}")
        if row["notes"]:
            lines.append(f"      note     : {row['notes']}")
    lines.extend([
        "",
        "[steps.expect] metrics:",
        "  scope.capture checks any numeric key from the capture quality summary with { min = ..., max = ... }.",
        "  Common scope metrics: frequency_estimate_hz, frequency_error_ratio, voltage_vpp_v, voltage_mean_v, duty_cycle.",
        "  dmm.read checks numeric keys from the DMM reading payload. Common DMM metric: value.",
        "",
        "scope.capture [steps.expect_fft] metrics:",
        "  FFT checks analyze the saved NPY waveform.",
        "  Common metrics: peak_frequency_hz, peak_amplitude_v, thd_ratio, harmonic_2_amplitude_v.",
    ])
    return "\n".join(lines)


@dataclass(frozen=True)
class SafetyGuard:
    scope_guard_channel: int | None
    require_scope_coupling_not: tuple[str, ...]
    allow_50ohm: bool = False


@dataclass(frozen=True)
class SourceRestorePolicy:
    source_state: bool
    source_channels: tuple[int, ...]

    @property
    def source_channel(self) -> int | None:
        return self.source_channels[0] if self.source_channels else None


@dataclass(frozen=True)
class RunStep:
    index: int
    kind: str
    fields: dict[str, Any]


@dataclass(frozen=True)
class RunPlan:
    path: Path
    name: str
    label: str
    safety: SafetyGuard
    restore: SourceRestorePolicy
    steps: list[RunStep]


def load_run_plan(path: str | Path) -> RunPlan:
    plan_path = Path(path)
    if not plan_path.exists():
        raise ConfigError(f"run plan not found: {plan_path}")
    try:
        raw = tomllib.loads(plan_path.read_bytes().decode("utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {plan_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("run plan must be a TOML table")

    experiment = _table(raw.get("experiment", {}), "experiment")
    name = str(experiment.get("name", plan_path.stem)).strip()
    label = str(experiment.get("label", name)).strip()
    if not name:
        raise ConfigError("experiment.name must not be empty")
    if not label:
        raise ConfigError("experiment.label must not be empty")

    safety = _parse_safety(raw.get("safety", {}))
    restore = _parse_restore(raw.get("restore", {}))
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ConfigError("run plan requires at least one [[steps]] entry")
    steps = [_parse_step(index, item) for index, item in enumerate(steps_raw)]
    _validate_frequency_response_steps(steps)
    return RunPlan(path=plan_path, name=name, label=label, safety=safety, restore=restore, steps=steps)


def _parse_restore(raw: Any) -> SourceRestorePolicy:
    table = _table(raw, "restore")
    allowed = {"source_state", "source_channel", "source_channels"}
    _reject_unknown_keys(table, allowed, "restore")

    source_state = table.get("source_state", False)
    if not isinstance(source_state, bool):
        raise ConfigError("restore.source_state must be true or false")
    source_channel = table.get("source_channel")
    source_channels_raw = table.get("source_channels")
    if source_channel is not None and source_channels_raw is not None:
        raise ConfigError("restore.source_channel and restore.source_channels are mutually exclusive")

    source_channels: tuple[int, ...] = ()
    if source_channel is not None:
        source_channels = (_positive_int(source_channel, "restore.source_channel"),)
    elif source_channels_raw is not None:
        if not isinstance(source_channels_raw, list) or not source_channels_raw:
            raise ConfigError("restore.source_channels must be a non-empty array of positive integers")
        parsed = tuple(_positive_int(item, "restore.source_channels") for item in source_channels_raw)
        if len(set(parsed)) != len(parsed):
            raise ConfigError("restore.source_channels must not contain duplicate channels")
        source_channels = parsed

    if source_channels and not source_state:
        raise ConfigError("restore source channel settings require restore.source_state = true")
    return SourceRestorePolicy(source_state=source_state, source_channels=source_channels)


def _parse_safety(raw: Any) -> SafetyGuard:
    table = _table(raw, "safety")
    allowed = {"scope_guard_channel", "require_scope_coupling_not", "allow_50ohm"}
    _reject_unknown_keys(table, allowed, "safety")

    channel = table.get("scope_guard_channel")
    if channel is not None:
        channel = _positive_int(channel, "safety.scope_guard_channel")

    blocked_raw = table.get("require_scope_coupling_not", [])
    if isinstance(blocked_raw, str):
        blocked = (blocked_raw.strip().upper(),)
    elif isinstance(blocked_raw, list):
        blocked = tuple(str(item).strip().upper() for item in blocked_raw)
    else:
        raise ConfigError("safety.require_scope_coupling_not must be a string or list of strings")
    if any(not item for item in blocked):
        raise ConfigError("safety.require_scope_coupling_not entries must not be empty")
    if blocked and channel is None:
        raise ConfigError(
            "safety.scope_guard_channel is required when require_scope_coupling_not is set"
        )
    allow_50ohm = table.get("allow_50ohm", False)
    if not isinstance(allow_50ohm, bool):
        raise ConfigError("safety.allow_50ohm must be true or false")
    return SafetyGuard(scope_guard_channel=channel, require_scope_coupling_not=blocked, allow_50ohm=allow_50ohm)


def _parse_step(index: int, raw: Any) -> RunStep:
    table = _table(raw, f"steps[{index}]")
    kind = str(table.get("kind", "")).strip()
    if not kind:
        raise ConfigError(
            f"steps[{index}].kind is required. Run `python -m wavebench run schema` "
            "to list supported step kinds."
        )
    if kind not in ALLOWED_STEP_KINDS:
        allowed = ", ".join(sorted(ALLOWED_STEP_KINDS))
        closest = difflib.get_close_matches(kind, sorted(ALLOWED_STEP_KINDS), n=1)
        suggestion = f" Did you mean '{closest[0]}'?" if closest else ""
        raise ConfigError(
            f"steps[{index}].kind '{kind}' is not supported.{suggestion} "
            f"Supported kinds: {allowed}. Run `python -m wavebench run schema` for field details."
        )

    schema = STEP_SCHEMAS[kind]
    allowed_fields = {"kind", *schema.required, *schema.optional}
    _reject_unknown_keys(table, allowed_fields, f"steps[{index}]")
    for field in schema.required:
        if field not in table:
            required = ", ".join(schema.required) or "-"
            optional = ", ".join(sorted(schema.optional)) or "-"
            raise ConfigError(
                f"steps[{index}] {kind} missing required field '{field}'. "
                f"Required fields: {required}. Optional fields: {optional}. "
                "Run `python -m wavebench run schema` for examples."
            )

    fields = {key: value for key, value in table.items() if key != "kind"}
    _normalize_step_fields(index, kind, fields)
    return RunStep(index=index, kind=kind, fields=fields)


def _normalize_step_fields(index: int, kind: str, fields: dict[str, Any]) -> None:
    prefix = f"steps[{index}]"
    if "on_failure" in fields:
        on_failure = _non_empty_str(fields["on_failure"], f"{prefix}.on_failure").lower()
        if on_failure not in {"stop", "continue"}:
            raise ConfigError(f"{prefix}.on_failure must be 'stop' or 'continue'")
        fields["on_failure"] = on_failure
    if "channel" in fields:
        fields["channel"] = _positive_int(fields["channel"], f"{prefix}.channel")
    if kind == "scope.capture":
        if "label" in fields:
            fields["label"] = _non_empty_str(fields["label"], f"{prefix}.label")
        if "points" in fields:
            fields["points"] = normalize_waveform_points(
                _non_empty_str(fields["points"], f"{prefix}.points")
            )
        for field in (
            "time_range_s",
            "expect_frequency_hz",
            "window_frequency_hz",
            "target_cycles",
            "frequency_tolerance",
        ):
            if field in fields:
                fields[field] = _positive_float(fields[field], f"{prefix}.{field}")
        if "target_vpp" in fields:
            fields["target_vpp"] = _positive_float(fields["target_vpp"], f"{prefix}.target_vpp")
            fields.setdefault("vertical_scale_v_per_div", fields["target_vpp"] / 5.0)
        if "vertical_scale_v_per_div" in fields:
            fields["vertical_scale_v_per_div"] = _positive_float(fields["vertical_scale_v_per_div"], f"{prefix}.vertical_scale_v_per_div")
        if "target_cycles" in fields:
            window_frequency = fields.get("window_frequency_hz") or fields.get("expect_frequency_hz")
            if window_frequency is None:
                raise ConfigError(
                    f"{prefix}.target_cycles requires window_frequency_hz or expect_frequency_hz"
                )
            if "time_range_s" not in fields:
                fields["time_range_s"] = fields["target_cycles"] / window_frequency
        for field in (
            "save_csv",
            "save_npy",
            "screenshot",
            "quality_gate",
            "auto_recover",
            "autoscale_before_capture",
        ):
            if field in fields and not isinstance(fields[field], bool):
                raise ConfigError(f"{prefix}.{field} must be true or false")
        if "autoscale_settle_s" in fields:
            autoscale_settle_s = _finite_float(
                fields["autoscale_settle_s"], f"{prefix}.autoscale_settle_s"
            )
            if autoscale_settle_s < 0:
                raise ConfigError(f"{prefix}.autoscale_settle_s must be >= 0")
            fields["autoscale_settle_s"] = autoscale_settle_s
        if "expect" in fields:
            fields["expect"] = _parse_expect(fields["expect"], f"{prefix}.expect")
        if "expect_fft" in fields:
            fields["expect_fft"] = _parse_expect(fields["expect_fft"], f"{prefix}.expect_fft")
    elif kind == "sweep.frequency_response":
        _normalize_frequency_response_fields(prefix, fields)
    elif kind == "power.set":
        fields["voltage_v"] = _positive_float(fields["voltage_v"], f"{prefix}.voltage_v")
        fields["current_limit_a"] = _positive_float(
            fields["current_limit_a"], f"{prefix}.current_limit_a"
        )
    elif kind in {"power.output", "source.output"}:
        state = _non_empty_str(fields["state"], f"{prefix}.state").lower()
        if state not in {"on", "off"}:
            raise ConfigError(f"{prefix}.state must be 'on' or 'off'")
        fields["state"] = state
    elif kind == "source.set_freq":
        fields["frequency_hz"] = _positive_float(fields["frequency_hz"], f"{prefix}.frequency_hz")
    elif kind == "source.arb_load":
        fields["file"] = _non_empty_str(fields["file"], f"{prefix}.file")
        fields["frequency_hz"] = _positive_float(fields["frequency_hz"], f"{prefix}.frequency_hz")
        fields["amplitude_vpp"] = _positive_float(fields["amplitude_vpp"], f"{prefix}.amplitude_vpp")
        if "offset_v" in fields:
            fields["offset_v"] = _finite_float(fields["offset_v"], f"{prefix}.offset_v")
        if "sample_rate_hz" in fields:
            fields["sample_rate_hz"] = _positive_float(fields["sample_rate_hz"], f"{prefix}.sample_rate_hz")
        if "max_points" in fields:
            fields["max_points"] = _positive_int(fields["max_points"], f"{prefix}.max_points")
        if "byte_order" in fields:
            byte_order = _non_empty_str(fields["byte_order"], f"{prefix}.byte_order").lower()
            if byte_order not in {"little", "big"}:
                raise ConfigError(f"{prefix}.byte_order must be little or big")
            fields["byte_order"] = byte_order
        if "output_on" in fields and not isinstance(fields["output_on"], bool):
            raise ConfigError(f"{prefix}.output_on must be true or false")
    elif kind == "source.set_func":
        fields["function"] = _non_empty_str(fields["function"], f"{prefix}.function")
    elif kind == "source.set_vpp":
        fields["value_vpp"] = _positive_float(fields["value_vpp"], f"{prefix}.value_vpp")
    elif kind == "source.set_duty":
        fields["duty_percent"] = _duty_percent(fields["duty_percent"], f"{prefix}.duty_percent")
    elif kind == "dmm.read":
        fields["function"] = _non_empty_str(fields.get("function", "dcv"), f"{prefix}.function").lower()
        if "expect" in fields:
            fields["expect"] = _parse_expect(fields["expect"], f"{prefix}.expect")
    elif kind == "sleep":
        fields["duration_s"] = _positive_float(fields["duration_s"], f"{prefix}.duration_s")


def _validate_frequency_response_steps(steps: list[RunStep]) -> None:
    response_steps = [step for step in steps if step.kind == "sweep.frequency_response"]
    labels: set[str] = set()
    for step in response_steps:
        label = str(step.fields.get("label", f"frequency_response_{step.index:02d}"))
        if label in labels:
            raise ConfigError(f"sweep.frequency_response labels must be unique: {label!r}")
        labels.add(label)


def _normalize_frequency_response_fields(prefix: str, fields: dict[str, Any]) -> None:
    for name in ("source_channel", "reference_channel", "response_channel"):
        if name in fields:
            fields[name] = _positive_int(fields[name], f"{prefix}.{name}")
    if fields["reference_channel"] == fields["response_channel"]:
        raise ConfigError(f"{prefix}.reference_channel and response_channel must differ")
    if "label" in fields:
        fields["label"] = _non_empty_str(fields["label"], f"{prefix}.label")

    explicit = fields.get("frequencies_hz")
    generated_names = {"start_frequency_hz", "stop_frequency_hz", "frequency_count", "spacing"}
    has_generated = any(name in fields for name in generated_names)
    if explicit is not None and has_generated:
        raise ConfigError(
            f"{prefix} must use either frequencies_hz or start/stop/frequency_count, not both"
        )
    if explicit is not None:
        if not isinstance(explicit, list) or len(explicit) < 2:
            raise ConfigError(f"{prefix}.frequencies_hz must be an array with at least two frequencies")
        frequencies = [_positive_float(value, f"{prefix}.frequencies_hz") for value in explicit]
    else:
        required = ("start_frequency_hz", "stop_frequency_hz", "frequency_count")
        missing = [name for name in required if name not in fields]
        if missing:
            raise ConfigError(
                f"{prefix} requires frequencies_hz or start_frequency_hz, stop_frequency_hz, and frequency_count"
            )
        start = _positive_float(fields["start_frequency_hz"], f"{prefix}.start_frequency_hz")
        stop = _positive_float(fields["stop_frequency_hz"], f"{prefix}.stop_frequency_hz")
        count = _positive_int(fields["frequency_count"], f"{prefix}.frequency_count")
        if stop <= start:
            raise ConfigError(f"{prefix}.stop_frequency_hz must be greater than start_frequency_hz")
        if count < 2:
            raise ConfigError(f"{prefix}.frequency_count must be >= 2")
        spacing = _non_empty_str(fields.get("spacing", "log"), f"{prefix}.spacing").lower()
        if spacing not in {"log", "linear"}:
            raise ConfigError(f"{prefix}.spacing must be 'log' or 'linear'")
        fields["start_frequency_hz"] = start
        fields["stop_frequency_hz"] = stop
        fields["frequency_count"] = count
        fields["spacing"] = spacing
        if spacing == "log":
            step = (log10(stop) - log10(start)) / (count - 1)
            frequencies = [10.0 ** (log10(start) + index * step) for index in range(count)]
        else:
            step = (stop - start) / (count - 1)
            frequencies = [start + index * step for index in range(count)]
    if any(second <= first for first, second in zip(frequencies, frequencies[1:])):
        raise ConfigError(f"{prefix}.frequencies_hz must be strictly increasing and unique")
    fields["frequencies_hz"] = frequencies

    _normalize_frequency_response_amplitudes(prefix, fields)

    fields["target_cycles"] = _positive_float(
        fields.get("target_cycles", 10.0), f"{prefix}.target_cycles"
    )
    fields["min_signal_vpp"] = _positive_float(
        fields.get("min_signal_vpp", 0.02), f"{prefix}.min_signal_vpp"
    )
    settle_s = _finite_float(fields.get("settle_s", 0.3), f"{prefix}.settle_s")
    if settle_s < 0:
        raise ConfigError(f"{prefix}.settle_s must be >= 0")
    fields["settle_s"] = settle_s
    if "frequency_tolerance" in fields:
        fields["frequency_tolerance"] = _positive_float(
            fields["frequency_tolerance"], f"{prefix}.frequency_tolerance"
        )
    if "points" in fields:
        fields["points"] = normalize_waveform_points(
            _non_empty_str(fields["points"], f"{prefix}.points")
        )
    retry_warning = fields.get("retry_warning_with_autoscale", True)
    if not isinstance(retry_warning, bool):
        raise ConfigError(f"{prefix}.retry_warning_with_autoscale must be true or false")
    fields["retry_warning_with_autoscale"] = retry_warning
    for name in ("save_csv", "screenshot"):
        if name in fields and not isinstance(fields[name], bool):
            raise ConfigError(f"{prefix}.{name} must be true or false")
    if "fit" in fields:
        fields["fit"] = _parse_frequency_response_fit(fields["fit"], f"{prefix}.fit")
    if "calibration" in fields:
        fields["calibration"] = normalize_frequency_response_calibration(
            fields["calibration"], f"{prefix}.calibration"
        ).as_dict()
    if "baseline" in fields:
        fields["baseline"] = normalize_frequency_response_baseline(
            fields["baseline"], f"{prefix}.baseline"
        ).as_dict()
    if "adaptive" in fields:
        fields["adaptive"] = normalize_frequency_response_adaptive(
            fields["adaptive"], f"{prefix}.adaptive"
        ).as_dict()
        if fields["adaptive"]["max_frequency_points"] < len(fields["frequencies_hz"]):
            raise ConfigError(
                f"{prefix}.adaptive.max_frequency_points must be at least the initial frequency count"
            )
    if "stop_conditions" in fields:
        fields["stop_conditions"] = _normalize_frequency_response_stop_conditions(
            fields["stop_conditions"], f"{prefix}.stop_conditions"
        )
    if "resume_from" in fields:
        fields["resume_from"] = _non_empty_str(fields["resume_from"], f"{prefix}.resume_from")


def _normalize_frequency_response_stop_conditions(raw: Any, name: str) -> dict[str, Any]:
    """Normalize explicit group-stop limits while retaining point-level tolerance."""

    table = _table(raw, name)
    _reject_unknown_keys(
        table,
        {
            "max_failed_points",
            "max_warning_points",
            "max_consecutive_failed_points",
            "max_gain_jump_db",
        },
        name,
    )
    result: dict[str, Any] = {}
    for key in ("max_failed_points", "max_warning_points", "max_consecutive_failed_points"):
        if key in table:
            value = _positive_int(table[key], f"{name}.{key}")
            result[key] = value
    if "max_gain_jump_db" in table:
        value = _positive_float(table["max_gain_jump_db"], f"{name}.max_gain_jump_db")
        result["max_gain_jump_db"] = value
    if not result:
        raise ConfigError(f"{name} must define at least one stop condition")
    return result


def _normalize_frequency_response_amplitudes(prefix: str, fields: dict[str, Any]) -> None:
    explicit = fields.get("amplitudes_vpp")
    generated_names = {"start_vpp", "stop_vpp", "vpp_step"}
    has_generated = any(name in fields for name in generated_names)
    if explicit is not None and has_generated:
        raise ConfigError(
            f"{prefix} must use either amplitudes_vpp or start_vpp, stop_vpp, and vpp_step, not both"
        )
    amplitudes: list[float] | None = None
    if explicit is not None:
        if not isinstance(explicit, list) or not explicit:
            raise ConfigError(f"{prefix}.amplitudes_vpp must be a non-empty array")
        amplitudes = [_positive_float(value, f"{prefix}.amplitudes_vpp") for value in explicit]
    elif has_generated:
        required = ("start_vpp", "stop_vpp", "vpp_step")
        missing = [name for name in required if name not in fields]
        if missing:
            raise ConfigError(f"{prefix} requires start_vpp, stop_vpp, and vpp_step together")
        start = _positive_float(fields["start_vpp"], f"{prefix}.start_vpp")
        stop = _positive_float(fields["stop_vpp"], f"{prefix}.stop_vpp")
        step = _positive_float(fields["vpp_step"], f"{prefix}.vpp_step")
        if stop <= start:
            raise ConfigError(f"{prefix}.stop_vpp must be greater than start_vpp")
        count = round((stop - start) / step)
        if count < 1 or abs(start + count * step - stop) > max(1e-12, step * 1e-9):
            raise ConfigError(f"{prefix}.vpp_step must divide the requested Vpp range exactly")
        amplitudes = [round(start + index * step, 15) for index in range(count + 1)]
    if amplitudes is not None:
        if any(second <= first for first, second in zip(amplitudes, amplitudes[1:])):
            raise ConfigError(f"{prefix}.amplitudes_vpp must be strictly increasing and unique")
        fields["amplitudes_vpp"] = amplitudes
    if amplitudes is not None or "autoscale_each_amplitude" in fields:
        autoscale = fields.get("autoscale_each_amplitude", True)
        if not isinstance(autoscale, bool):
            raise ConfigError(f"{prefix}.autoscale_each_amplitude must be true or false")
        fields["autoscale_each_amplitude"] = autoscale


def _parse_frequency_response_fit(raw: Any, name: str) -> dict[str, Any]:
    table = _table(raw, name)
    _reject_unknown_keys(table, {"methods", "polynomial_degree"}, name)
    methods_raw = table.get("methods", list(FIT_METHODS))
    if not isinstance(methods_raw, list) or not methods_raw:
        raise ConfigError(f"{name}.methods must be a non-empty array")
    methods = [_non_empty_str(value, f"{name}.methods").lower() for value in methods_raw]
    if any(method not in FIT_METHODS for method in methods):
        raise ConfigError(f"{name}.methods must use: {', '.join(FIT_METHODS)}")
    if len(set(methods)) != len(methods):
        raise ConfigError(f"{name}.methods must not contain duplicates")
    degree = _positive_int(table.get("polynomial_degree", 3), f"{name}.polynomial_degree")
    if degree > 5:
        raise ConfigError(f"{name}.polynomial_degree must be <= 5")
    return {"methods": methods, "polynomial_degree": degree}


def _parse_expect(raw: Any, name: str) -> dict[str, dict[str, float]]:
    table = _table(raw, name)
    if not table:
        raise ConfigError(f"{name} must not be empty")
    result: dict[str, dict[str, float]] = {}
    for metric, limits_raw in table.items():
        metric_name = _non_empty_str(metric, f"{name} metric")
        limits = _table(limits_raw, f"{name}.{metric_name}")
        _reject_unknown_keys(limits, {"min", "max"}, f"{name}.{metric_name}")
        if "min" not in limits and "max" not in limits:
            raise ConfigError(f"{name}.{metric_name} requires min or max")
        parsed: dict[str, float] = {}
        for key in ("min", "max"):
            if key in limits:
                parsed[key] = _finite_float(limits[key], f"{name}.{metric_name}.{key}")
        if "min" in parsed and "max" in parsed and parsed["min"] > parsed["max"]:
            raise ConfigError(f"{name}.{metric_name}.min must be <= max")
        result[metric_name] = parsed
    return result


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ConfigError(f"{name} must be finite")
    return result


def _table(raw: Any, name: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be a TOML table")
    return raw


def _reject_unknown_keys(table: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        allowed_text = ", ".join(sorted(allowed)) or "-"
        suggestions = _unknown_key_suggestions(unknown, allowed)
        suggestion_text = f" {suggestions}" if suggestions else ""
        hint = " Run `python -m wavebench run schema` for field details." if name.startswith("steps[") else ""
        raise ConfigError(
            f"{name} has unknown key(s): {', '.join(unknown)}.{suggestion_text} "
            f"Allowed keys: {allowed_text}.{hint}"
        )


def _unknown_key_suggestions(unknown: list[str], allowed: set[str]) -> str:
    parts = []
    choices = sorted(allowed)
    for key in unknown:
        closest = difflib.get_close_matches(key, choices, n=1)
        if closest:
            parts.append(f"'{key}' -> '{closest[0]}'")
    if not parts:
        return ""
    return "Did you mean " + ", ".join(parts) + "?"


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if result < 1:
        raise ConfigError(f"{name} must be >= 1")
    return result


def _positive_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a positive number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive number") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ConfigError(f"{name} must be finite")
    if result <= 0:
        raise ConfigError(f"{name} must be > 0")
    return result


def _non_empty_str(value: Any, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ConfigError(f"{name} must not be empty")
    return result


def _duty_percent(value: Any, name: str) -> float:
    result = _positive_float(value, name)
    if result >= 100:
        raise ConfigError(f"{name} must be < 100")
    return result
