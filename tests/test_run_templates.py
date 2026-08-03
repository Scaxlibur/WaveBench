from pathlib import Path

import pytest

from wavebench.errors import ConfigError
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_templates import (
    RunTemplateOptions,
    list_run_templates,
    parse_frequencies,
    render_run_template,
    write_run_template,
)


def test_list_run_templates_includes_public_names():
    names = [item.name for item in list_run_templates()]

    assert names == [
        "dmm-acv-source",
        "power-dmm-dcv",
        "source-scope-frequency-response",
        "source-scope-sine",
        "source-scope-sweep",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "source-scope-sine",
        "source-scope-sweep",
        "source-scope-frequency-response",
        "dmm-acv-source",
        "power-dmm-dcv",
    ],
)
def test_run_templates_render_valid_plans(tmp_path: Path, name: str):
    output = write_run_template(name, tmp_path / f"{name}.toml")

    plan = load_run_plan(output)

    assert plan.name
    assert plan.steps
    assert render_run_template(name).endswith("\n")


def test_write_run_template_refuses_overwrite_without_force(tmp_path: Path):
    output = write_run_template("source-scope-sine", tmp_path / "plan.toml")

    with pytest.raises(ConfigError, match="--force"):
        write_run_template("source-scope-sine", output)


def test_write_run_template_overwrites_with_force(tmp_path: Path):
    output = tmp_path / "plan.toml"
    output.write_text("old", encoding="utf-8")

    write_run_template("power-dmm-dcv", output, force=True)

    assert "power_dmm_dcv_3_3v" in output.read_text(encoding="utf-8")


def test_source_scope_template_applies_frequency_vpp_and_channels(tmp_path: Path):
    output = write_run_template(
        "source-scope-sine",
        tmp_path / "plan.toml",
        options=RunTemplateOptions(frequency_hz=10_000, vpp=3.3, source_channel=2, scope_channel=1),
    )

    text = output.read_text(encoding="utf-8")
    plan = load_run_plan(output)

    assert plan.name == "source_scope_sine_10k"
    assert "frequency_hz = 10000" in text
    assert "value_vpp = 3.3" in text
    assert "source_channel = 2" in text
    assert any(step.kind == "scope.capture" and step.fields["target_vpp"] == 3.3 for step in plan.steps)


def test_source_scope_sweep_template_expands_frequency_points(tmp_path: Path):
    output = write_run_template(
        "source-scope-sweep",
        tmp_path / "sweep.toml",
        options=RunTemplateOptions(frequencies_hz=(100.0, 1000.0, 10000.0), vpp=1.0),
    )

    text = output.read_text(encoding="utf-8")
    plan = load_run_plan(output)

    assert plan.name == "source_scope_sweep_100hz_to_10k"
    assert [step.fields.get("frequency_hz") for step in plan.steps if step.kind == "source.set_freq"] == [
        100.0,
        1000.0,
        10000.0,
    ]
    assert [step.fields.get("label") for step in plan.steps if step.kind == "scope.capture"] == [
        "sweep_100hz",
        "sweep_1k",
        "sweep_10k",
    ]
    assert text.count("[steps.expect_fft]") == 3


def test_frequency_response_template_uses_two_scope_channels_and_compatible_fit_degree(tmp_path: Path):
    output = write_run_template(
        "source-scope-frequency-response",
        tmp_path / "response.toml",
        options=RunTemplateOptions(
            frequencies_hz=(100.0, 1000.0, 10000.0),
            source_channel=2,
            reference_channel=1,
            response_channel=3,
            frequency_response_fit=True,
        ),
    )

    plan = load_run_plan(output)
    response = next(step for step in plan.steps if step.kind == "sweep.frequency_response")

    assert response.fields["source_channel"] == 2
    assert response.fields["reference_channel"] == 1
    assert response.fields["response_channel"] == 3
    assert response.fields["frequencies_hz"] == [100.0, 1000.0, 10000.0]
    assert response.fields["fit"] == {
        "methods": ["linear_log", "polynomial", "pchip"],
        "polynomial_degree": 2,
    }


@pytest.mark.parametrize("frequencies", [(1000.0,), (1000.0, 1000.0), (1000.0, 100.0)])
def test_frequency_response_template_rejects_non_sweep_frequency_lists(frequencies: tuple[float, ...]):
    with pytest.raises(ConfigError, match="frequency-response template"):
        render_run_template(
            "source-scope-frequency-response",
            options=RunTemplateOptions(frequencies_hz=frequencies),
        )


def test_parse_frequencies_accepts_comma_separated_values():
    assert parse_frequencies("100, 1000,10000") == (100.0, 1000.0, 10000.0)


@pytest.mark.parametrize("value", ["100,,1000", "abc", "0"])
def test_parse_frequencies_rejects_invalid_values(value: str):
    with pytest.raises(ConfigError):
        parse_frequencies(value)


def test_dmm_acv_template_scales_rms_expectation():
    text = render_run_template("dmm-acv-source", options=RunTemplateOptions(vpp=2.0, source_channel=1))

    assert "source_channel = 1" in text
    assert "value_vpp = 2" in text
    assert "value = { min = 0.671751, max = 0.742462 }" in text


def test_power_template_applies_voltage_current_and_channel():
    text = render_run_template(
        "power-dmm-dcv",
        options=RunTemplateOptions(power_channel=2, voltage_v=5.0, current_limit_a=0.2),
    )

    assert "power_dmm_dcv_5v" in text
    assert "channel = 2" in text
    assert "voltage_v = 5" in text
    assert "current_limit_a = 0.2" in text
    assert "value = { min = 4.9, max = 5.1 }" in text


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (RunTemplateOptions(frequency_hz=0), "--frequency"),
        (RunTemplateOptions(vpp=0), "--vpp"),
        (RunTemplateOptions(source_channel=0), "--source-channel"),
        (RunTemplateOptions(voltage_v=0), "--voltage"),
        (RunTemplateOptions(current_limit_a=0), "--current-limit"),
    ],
)
def test_run_template_validates_parameters(options: RunTemplateOptions, message: str):
    with pytest.raises(ConfigError, match=message):
        render_run_template("source-scope-sine", options=options)


def test_unknown_run_template_reports_choices():
    with pytest.raises(ConfigError, match="unknown run template"):
        render_run_template("missing")
