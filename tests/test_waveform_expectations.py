import numpy as np

from wavebench.data.expectations import (
    estimate_triangle_symmetry_percent,
    evaluate_waveform_expectation,
    expectation_summary,
)
from wavebench.instruments.models import WaveformData, WaveformHeader


def _waveform(channel: int, times: np.ndarray, values: np.ndarray) -> WaveformData:
    return WaveformData(
        channel=channel,
        header=WaveformHeader(x_start=float(times[0]), x_stop=float(times[-1]), points=int(times.size)),
        voltages_v=values,
    )


def test_square_wave_expectation_passes_frequency_vpp_mean_and_duty():
    times = np.linspace(0.0, 0.009999, 10_000)
    values = np.where((times * 1000.0) % 1.0 < 0.5, 0.5, -0.5)
    waveform = _waveform(1, times, values)

    result = evaluate_waveform_expectation(
        waveform,
        {
            "label": "1k square",
            "shape": "square",
            "frequency_hz": 1000,
            "frequency_tolerance_ratio": 0.02,
            "vpp_v": 1.0,
            "vpp_tolerance_ratio": 0.05,
            "mean_v": 0.0,
            "mean_tolerance_v": 0.02,
            "duty_percent": 50,
            "duty_tolerance": 0.02,
        },
    )

    assert result["status"] == "pass"
    assert {check["metric"] for check in result["checks"]} == {
        "frequency_hz",
        "vpp_v",
        "mean_v",
        "duty_cycle",
    }


def test_triangle_symmetry_expectation_passes_for_asymmetric_ramp():
    times = np.linspace(0.0, 0.0002, 5000)
    period = 20e-6
    symmetry = 30.0
    phase = (times % period) / period
    values = np.where(
        phase < symmetry / 100.0,
        -0.5 + phase / (symmetry / 100.0),
        0.5 - (phase - symmetry / 100.0) / (1.0 - symmetry / 100.0),
    )
    values += 0.5
    waveform = _waveform(2, times, values)

    measured = estimate_triangle_symmetry_percent(waveform)
    result = evaluate_waveform_expectation(
        waveform,
        {
            "label": "50k triangle",
            "shape": "triangle",
            "frequency_hz": 50_000,
            "vpp_v": 1.0,
            "mean_v": 0.5,
            "symmetry_percent": 30,
            "symmetry_tolerance_percent": 3,
        },
    )

    assert measured is not None
    assert abs(measured - 30.0) < 3.0
    assert result["status"] == "pass"


def test_expectation_warns_instead_of_failing_low_confidence_frequency():
    times = np.linspace(0.0, 0.0005, 200)
    values = np.sin(2 * np.pi * 1000 * times)
    waveform = _waveform(1, times, values)

    result = evaluate_waveform_expectation(waveform, {"frequency_hz": 1000})

    assert result["status"] == "warn"
    assert result["checks"][0]["status"] == "warn"
    assert "low confidence" in result["checks"][0]["message"]


def test_expectation_summary_rolls_up_channel_statuses():
    summary = expectation_summary(
        {
            1: {"status": "pass"},
            2: {"status": "warn"},
        }
    )

    assert summary == {"status": "warn", "channels": {"1": "pass", "2": "warn"}}
