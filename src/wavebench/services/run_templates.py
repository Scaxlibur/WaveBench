from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from pathlib import Path
import re
import textwrap

from wavebench.errors import ConfigError


@dataclass(frozen=True)
class RunTemplate:
    name: str
    description: str


@dataclass(frozen=True)
class RunTemplateOptions:
    frequency_hz: float = 1000.0
    frequencies_hz: tuple[float, ...] = ()
    vpp: float = 1.0
    source_channel: int | None = None
    scope_channel: int | None = None
    reference_channel: int | None = None
    response_channel: int | None = None
    frequency_response_fit: bool = False
    power_channel: int | None = None
    voltage_v: float = 3.3
    current_limit_a: float = 0.1


def list_run_templates() -> list[RunTemplate]:
    return [RUN_TEMPLATES[name] for name in sorted(RUN_TEMPLATES)]


def get_run_template(name: str) -> RunTemplate:
    try:
        return RUN_TEMPLATES[name]
    except KeyError as exc:
        choices = ", ".join(sorted(RUN_TEMPLATES))
        raise ConfigError(f"unknown run template: {name}; choices: {choices}") from exc


def render_run_template(name: str, options: RunTemplateOptions | None = None) -> str:
    get_run_template(name)
    opts = _validate_options(options or RunTemplateOptions())
    if name == "source-scope-sine":
        return _render_source_scope_sine(opts)
    if name == "source-scope-sweep":
        return _render_source_scope_sweep(opts)
    if name == "source-scope-frequency-response":
        return _render_source_scope_frequency_response(opts)
    if name == "dmm-acv-source":
        return _render_dmm_acv_source(opts)
    if name == "power-dmm-dcv":
        return _render_power_dmm_dcv(opts)
    raise AssertionError(f"unhandled run template: {name}")


def write_run_template(
    name: str,
    output: str | Path,
    *,
    force: bool = False,
    options: RunTemplateOptions | None = None,
) -> Path:
    output_path = Path(output)
    if output_path.exists() and not force:
        raise ConfigError(f"output already exists: {output_path}; pass --force to overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_run_template(name, options=options), encoding="utf-8")
    return output_path


RUN_TEMPLATES = {
    "source-scope-sine": RunTemplate(
        name="source-scope-sine",
        description="DG4202 source -> RTM2032 scope sine closure with FFT expectations",
    ),
    "source-scope-sweep": RunTemplate(
        name="source-scope-sweep",
        description="DG4202 source -> RTM2032 scope multi-frequency sine sweep as an expanded run plan",
    ),
    "source-scope-frequency-response": RunTemplate(
        name="source-scope-frequency-response",
        description="DG4202 source -> two scope channels frequency response with optional gain fitting",
    ),
    "dmm-acv-source": RunTemplate(
        name="dmm-acv-source",
        description="DG4202 source -> DMM ACV smoke with source-state restore",
    ),
    "power-dmm-dcv": RunTemplate(
        name="power-dmm-dcv",
        description="DP800 voltage-set plus DMM DCV readback; output state is not changed",
    ),
}


def parse_frequencies(value: str | None) -> tuple[float, ...]:
    if value is None or not value.strip():
        return ()
    frequencies: list[float] = []
    for item in value.split(","):
        stripped = item.strip()
        if not stripped:
            raise ConfigError("--frequencies must not contain empty items")
        try:
            frequency = float(stripped)
        except ValueError as exc:
            raise ConfigError(f"invalid --frequencies item: {stripped}") from exc
        if frequency <= 0:
            raise ConfigError("--frequencies items must be > 0")
        frequencies.append(frequency)
    return tuple(frequencies)


def _render_source_scope_sine(options: RunTemplateOptions) -> str:
    source_channel = options.source_channel or 1
    scope_channel = options.scope_channel or 1
    label = f"source_scope_sine_{_frequency_label(options.frequency_hz)}"
    freq_min = options.frequency_hz * 0.95
    freq_max = options.frequency_hz * 1.05
    fft_min = options.frequency_hz * 0.99
    fft_max = options.frequency_hz * 1.01
    vpp_min = options.vpp * 0.8
    vpp_max = options.vpp * 1.2
    peak_min = options.vpp * 0.40
    peak_max = options.vpp * 0.60
    return _clean(
        f"""
        # WaveBench template: DG4202 CH{source_channel} -> RTM2032 CH{scope_channel}, sine, {_fmt(options.vpp)} Vpp.
        # Confirm bench wiring before execution. This plan enables the source output
        # and restores the original source state afterwards.

        [experiment]
        name = "{label}"
        label = "{label}"

        [safety]
        scope_guard_channel = {scope_channel}
        require_scope_coupling_not = ["DC", "AC"]

        [restore]
        source_state = true
        source_channel = {source_channel}

        [[steps]]
        kind = "source.status"
        channel = {source_channel}

        [[steps]]
        kind = "source.set_func"
        channel = {source_channel}
        function = "sin"

        [[steps]]
        kind = "source.set_vpp"
        channel = {source_channel}
        value_vpp = {_fmt(options.vpp)}

        [[steps]]
        kind = "source.set_freq"
        channel = {source_channel}
        frequency_hz = {_fmt(options.frequency_hz)}

        [[steps]]
        kind = "source.output"
        channel = {source_channel}
        state = "on"

        [[steps]]
        kind = "sleep"
        duration_s = 0.3

        [[steps]]
        kind = "scope.capture"
        channel = {scope_channel}
        label = "{label}"
        points = "def"
        window_frequency_hz = {_fmt(options.frequency_hz)}
        target_cycles = 10
        expect_frequency_hz = {_fmt(options.frequency_hz)}
        frequency_tolerance = 0.05
        target_vpp = {_fmt(options.vpp)}
        save_csv = false
        save_npy = true
        screenshot = true
        quality_gate = true
        auto_recover = true

        [steps.expect]
        frequency_estimate_hz = {{ min = {_fmt(freq_min)}, max = {_fmt(freq_max)} }}
        frequency_error_ratio = {{ max = 0.05 }}
        voltage_vpp_v = {{ min = {_fmt(vpp_min)}, max = {_fmt(vpp_max)} }}

        [steps.expect_fft]
        peak_frequency_hz = {{ min = {_fmt(fft_min)}, max = {_fmt(fft_max)} }}
        peak_amplitude_v = {{ min = {_fmt(peak_min)}, max = {_fmt(peak_max)} }}
        thd_ratio = {{ max = 0.08 }}
        """
    )


def _render_source_scope_sweep(options: RunTemplateOptions) -> str:
    source_channel = options.source_channel or 1
    scope_channel = options.scope_channel or 1
    frequencies = options.frequencies_hz or (100.0, 1000.0, 10000.0)
    label = f"source_scope_sweep_{_frequency_label(frequencies[0])}_to_{_frequency_label(frequencies[-1])}"
    steps = "\n\n".join(
        _render_source_scope_sweep_point(freq, options.vpp, source_channel, scope_channel) for freq in frequencies
    )
    header = _clean(
        f"""
        # WaveBench template: DG4202 CH{source_channel} -> RTM2032 CH{scope_channel}, sine sweep, {_fmt(options.vpp)} Vpp.
        # Confirm bench wiring before execution. This plan enables the source output
        # and restores the original source state afterwards.

        [experiment]
        name = "{label}"
        label = "{label}"

        [safety]
        scope_guard_channel = {scope_channel}
        require_scope_coupling_not = ["DC", "AC"]

        [restore]
        source_state = true
        source_channel = {source_channel}

        [[steps]]
        kind = "source.status"
        channel = {source_channel}

        [[steps]]
        kind = "source.set_func"
        channel = {source_channel}
        function = "sin"

        [[steps]]
        kind = "source.set_vpp"
        channel = {source_channel}
        value_vpp = {_fmt(options.vpp)}

        [[steps]]
        kind = "source.output"
        channel = {source_channel}
        state = "on"
        """
    )
    return header + "\n" + steps + "\n"


def _render_source_scope_sweep_point(frequency_hz: float, vpp: float, source_channel: int, scope_channel: int) -> str:
    label = f"sweep_{_frequency_label(frequency_hz)}"
    freq_min = frequency_hz * 0.95
    freq_max = frequency_hz * 1.05
    fft_min = frequency_hz * 0.99
    fft_max = frequency_hz * 1.01
    vpp_min = vpp * 0.8
    vpp_max = vpp * 1.2
    peak_min = vpp * 0.40
    peak_max = vpp * 0.60
    return textwrap.dedent(
        f"""
        [[steps]]
        kind = "source.set_freq"
        channel = {source_channel}
        frequency_hz = {_fmt(frequency_hz)}

        [[steps]]
        kind = "sleep"
        duration_s = 0.3

        [[steps]]
        kind = "scope.capture"
        channel = {scope_channel}
        label = "{label}"
        points = "def"
        window_frequency_hz = {_fmt(frequency_hz)}
        target_cycles = 10
        expect_frequency_hz = {_fmt(frequency_hz)}
        frequency_tolerance = 0.05
        target_vpp = {_fmt(vpp)}
        save_csv = false
        save_npy = true
        screenshot = true
        quality_gate = true
        auto_recover = true

        [steps.expect]
        frequency_estimate_hz = {{ min = {_fmt(freq_min)}, max = {_fmt(freq_max)} }}
        frequency_error_ratio = {{ max = 0.05 }}
        voltage_vpp_v = {{ min = {_fmt(vpp_min)}, max = {_fmt(vpp_max)} }}

        [steps.expect_fft]
        peak_frequency_hz = {{ min = {_fmt(fft_min)}, max = {_fmt(fft_max)} }}
        peak_amplitude_v = {{ min = {_fmt(peak_min)}, max = {_fmt(peak_max)} }}
        thd_ratio = {{ max = 0.08 }}
        """
    ).strip()


def _render_source_scope_frequency_response(options: RunTemplateOptions) -> str:
    source_channel = options.source_channel or 1
    reference_channel = options.reference_channel or 1
    response_channel = options.response_channel or 2
    if reference_channel == response_channel:
        raise ConfigError("--reference-channel and --response-channel must differ")
    frequencies = options.frequencies_hz or (100.0, 1000.0, 10000.0)
    if len(frequencies) < 2:
        raise ConfigError("frequency-response template requires at least two --frequencies values")
    if any(second <= first for first, second in zip(frequencies, frequencies[1:])):
        raise ConfigError(
            "frequency-response template --frequencies values must be strictly increasing and unique"
        )
    label = (
        f"source_scope_frequency_response_{_frequency_label(frequencies[0])}_to_"
        f"{_frequency_label(frequencies[-1])}"
    )
    fit = ""
    if options.frequency_response_fit:
        polynomial_degree = min(3, len(frequencies) - 1)
        fit = """

        [steps.fit]
        methods = ["linear_log", "polynomial", "pchip"]
        polynomial_degree = {polynomial_degree}
        """.format(polynomial_degree=polynomial_degree)
    frequency_text = ", ".join(_fmt(value) for value in frequencies)
    return _clean(
        f"""
        # WaveBench template: DG4202 CH{source_channel} -> DUT -> scope CH{response_channel}.
        # Scope CH{reference_channel} measures the DUT input reference. Confirm both probe paths,
        # high-impedance inputs, vertical scales, and source output before execution.

        [experiment]
        name = "{label}"
        label = "{label}"

        [restore]
        source_state = true
        source_channel = {source_channel}

        [[steps]]
        kind = "source.status"
        channel = {source_channel}

        [[steps]]
        kind = "source.set_func"
        channel = {source_channel}
        function = "sin"

        [[steps]]
        kind = "source.set_vpp"
        channel = {source_channel}
        value_vpp = {_fmt(options.vpp)}

        [[steps]]
        kind = "source.output"
        channel = {source_channel}
        state = "on"

        [[steps]]
        kind = "sweep.frequency_response"
        label = "{label}"
        source_channel = {source_channel}
        reference_channel = {reference_channel}
        response_channel = {response_channel}
        frequencies_hz = [{frequency_text}]
        target_cycles = 10
        settle_s = 0.3
        points = "def"
        save_csv = false
        screenshot = false
        {fit}
        """
    )


def _render_dmm_acv_source(options: RunTemplateOptions) -> str:
    source_channel = options.source_channel or 2
    label = f"dmm_acv_source_{_frequency_label(options.frequency_hz)}"
    rms = options.vpp / (2.0 * sqrt(2.0))
    return _clean(
        f"""
        # WaveBench template: DG4202 CH{source_channel} -> DMM ACV smoke.
        # Confirm bench wiring before execution. This plan enables the source output
        # and restores the original source state afterwards.

        [experiment]
        name = "{label}"
        label = "{label}"

        [restore]
        source_state = true
        source_channel = {source_channel}

        [[steps]]
        kind = "source.status"
        channel = {source_channel}

        [[steps]]
        kind = "source.set_func"
        channel = {source_channel}
        function = "sin"

        [[steps]]
        kind = "source.set_freq"
        channel = {source_channel}
        frequency_hz = {_fmt(options.frequency_hz)}

        [[steps]]
        kind = "source.set_vpp"
        channel = {source_channel}
        value_vpp = {_fmt(options.vpp)}

        [[steps]]
        kind = "source.output"
        channel = {source_channel}
        state = "on"

        [[steps]]
        kind = "sleep"
        duration_s = 0.4

        [[steps]]
        kind = "dmm.read"
        function = "acv"

        [steps.expect]
        value = {{ min = {_fmt(rms * 0.95)}, max = {_fmt(rms * 1.05)} }}
        """
    )


def _render_power_dmm_dcv(options: RunTemplateOptions) -> str:
    power_channel = options.power_channel or 1
    label = f"power_dmm_dcv_{_voltage_label(options.voltage_v)}"
    return _clean(
        f"""
        # WaveBench template: DP800 CH{power_channel} -> DMM DCV readback.
        # Confirm bench wiring before execution. This plan sets voltage/current limit
        # but intentionally does not turn the power output on or off.

        [experiment]
        name = "{label}"
        label = "{label}"

        [[steps]]
        kind = "power.status"
        channel = {power_channel}

        [[steps]]
        kind = "power.set"
        channel = {power_channel}
        voltage_v = {_fmt(options.voltage_v)}
        current_limit_a = {_fmt(options.current_limit_a)}

        [[steps]]
        kind = "sleep"
        duration_s = 0.3

        [[steps]]
        kind = "dmm.read"
        function = "dcv"

        [steps.expect]
        value = {{ min = {_fmt(options.voltage_v - 0.1)}, max = {_fmt(options.voltage_v + 0.1)} }}
        """
    )


def _validate_options(options: RunTemplateOptions) -> RunTemplateOptions:
    if options.frequency_hz <= 0:
        raise ConfigError("--frequency must be > 0")
    for frequency in options.frequencies_hz:
        if frequency <= 0:
            raise ConfigError("--frequencies items must be > 0")
    if options.vpp <= 0:
        raise ConfigError("--vpp must be > 0")
    if options.voltage_v <= 0:
        raise ConfigError("--voltage must be > 0")
    if options.current_limit_a <= 0:
        raise ConfigError("--current-limit must be > 0")
    for label, value in (
        ("--source-channel", options.source_channel),
        ("--scope-channel", options.scope_channel),
        ("--reference-channel", options.reference_channel),
        ("--response-channel", options.response_channel),
        ("--power-channel", options.power_channel),
    ):
        if value is not None and value <= 0:
            raise ConfigError(f"{label} must be > 0")
    return options


def _clean(content: str) -> str:
    return textwrap.dedent(content).strip() + "\n"


def _fmt(value: float) -> str:
    return f"{value:.6g}"


def _frequency_label(value: float) -> str:
    if value >= 1000 and value % 1000 == 0:
        return _slug(f"{value / 1000:g}k")
    return _slug(f"{value:g}hz")


def _voltage_label(value: float) -> str:
    return _slug(f"{value:g}v")


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
