import numpy as np
import pytest

from wavebench.data.expectations import (
    estimate_triangle_symmetry_percent,
    evaluate_waveform_expectation,
    expectation_summary,
    validate_expectation,
)
from wavebench.errors import ConfigError
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


@pytest.mark.parametrize(("statuses", "expected"), [
    ([], "skipped"),
    (["skipped"], "skipped"),
    (["unavailable"], "unavailable"),
    (["unavailable", "skipped"], "unavailable"),
    (["unavailable", "pass"], "partial"),
    (["unavailable", "warn"], "partial"),
    (["unavailable", "fail"], "fail"),
])
def test_expectation_summary_accounts_for_unavailable_channels(statuses, expected):
    results = {channel: {"status": status} for channel, status in enumerate(statuses, 1)}

    assert expectation_summary(results) == {
        "status": expected,
        "channels": {str(channel): result["status"] for channel, result in results.items()},
    }


def test_validate_expectation_rejects_unknown_field():
    # 拼错的字段名不能被静默忽略
    with pytest.raises(ConfigError, match="unknown expectation field"):
        validate_expectation({"frequncy_hz": 1000})


@pytest.mark.parametrize(
    "expectation",
    [
        {"frequency_hz": "1000"},
        {"frequency_hz": True},
        {"frequency_hz": float("nan")},
        {"frequency_hz": float("inf")},
        {"vpp_v": -1.0},
        {"vpp_v": 0.0},
        {"frequency_tolerance_ratio": -0.1},
        {"duty_cycle": 1.5},
        {"duty_percent": 120.0},
        {"symmetry_percent": 150.0},
        {"symmetry_tolerance_percent": -1.0},
        {"label": ""},
    ],
)
def test_validate_expectation_rejects_invalid_values(expectation):
    with pytest.raises(ConfigError):
        validate_expectation(expectation)


def test_validate_expectation_rejects_conflicting_synonyms():
    with pytest.raises(ConfigError, match="must not set both"):
        validate_expectation({"duty_cycle": 0.5, "duty_percent": 50.0})
    with pytest.raises(ConfigError, match="must not set both"):
        validate_expectation({"mean_v": 0.0, "offset_v": 0.0})


def test_expectation_without_checkable_metric_is_skipped_not_passed():
    times = np.linspace(0.0, 0.001, 100)
    waveform = _waveform(1, times, np.sin(2 * np.pi * 1000 * times))

    # 只有 label/shape 时没有任何可执行检查，必须显式 skipped 而不是 pass
    result = evaluate_waveform_expectation(waveform, {"label": "sine", "shape": "sine"})

    assert result["status"] == "skipped"
    assert result["checks"] == []
    assert "no checkable metric" in result["message"]


def test_evaluate_waveform_expectation_rejects_typo_before_any_check():
    times = np.linspace(0.0, 0.001, 100)
    waveform = _waveform(1, times, np.sin(2 * np.pi * 1000 * times))

    with pytest.raises(ConfigError, match="unknown expectation field"):
        evaluate_waveform_expectation(waveform, {"frequncy_hz": 1000})


def _triangle(times: np.ndarray, *, period: float, symmetry_percent: float, vpp: float) -> np.ndarray:
    phase = (times % period) / period
    rising = symmetry_percent / 100.0
    values = np.where(
        phase < rising,
        phase / rising,
        1.0 - (phase - rising) / (1.0 - rising),
    )
    return values * vpp - vpp / 2.0


@pytest.mark.parametrize("symmetry", [10.0, 50.0, 90.0])
def test_triangle_symmetry_is_robust_to_noise_quantization_and_overshoot(symmetry):
    period = 20e-6
    times = np.linspace(0.0, 20 * period, 20_000, endpoint=False)
    period = float(times[1] - times[0]) * 1000.0
    clean = _triangle(times, period=period, symmetry_percent=symmetry, vpp=2.0)

    rng = np.random.default_rng(20260928)
    # 1 mV 噪声 + 1 mV 量化台阶：逐点差分符号会被噪声打乱
    noisy = np.round(clean + rng.normal(0.0, 1e-3, clean.size), 3)
    # 5 mV 噪声 + 轻微过冲
    overshoot = clean + rng.normal(0.0, 5e-3, clean.size) + 0.02 * np.sin(2 * np.pi * 5 / period * times)

    for values in (noisy, overshoot):
        measured = estimate_triangle_symmetry_percent(
            _waveform(1, times, values),
            expected_frequency_hz=1.0 / period,
        )
        assert measured is not None
        assert abs(measured - symmetry) < 3.0, (symmetry, measured)


def test_triangle_symmetry_uses_expected_frequency_to_ignore_short_glitches():
    period = 20e-6
    times = np.linspace(0.0, 20 * period, 20_000, endpoint=False)
    period = float(times[1] - times[0]) * 1000.0
    values = _triangle(times, period=period, symmetry_percent=10.0, vpp=2.0)
    # 在上升沿插入一个远窄于半周期的毛刺；期望频率约束应把它排除在极值序列之外
    values[times.size // 2 : times.size // 2 + 20] += 0.3

    measured = estimate_triangle_symmetry_percent(
        _waveform(1, times, values),
        expected_frequency_hz=1.0 / period,
    )

    assert measured is not None
    assert abs(measured - 10.0) < 3.0
