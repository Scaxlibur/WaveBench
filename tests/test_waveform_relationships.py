import numpy as np
import pytest

from wavebench.data.relationships import analyze_waveform_pair, analyze_waveform_relationships
from wavebench.instruments.models import WaveformData, WaveformHeader


def _waveform(channel: int, values: np.ndarray, *, stop: float = 0.009) -> WaveformData:
    return WaveformData(
        channel=channel,
        header=WaveformHeader(x_start=0.0, x_stop=stop, points=int(values.size)),
        voltages_v=values,
    )


def test_waveform_pair_reports_frequency_voltage_and_phase_for_related_signals():
    t = np.linspace(0.0, 0.009, 1000)
    left = _waveform(1, np.sin(2 * np.pi * 1000 * t), stop=float(t[-1]))
    right = _waveform(2, 0.5 * np.sin(2 * np.pi * 1000 * (t - 0.00025)) + 0.2, stop=float(t[-1]))

    relationship = analyze_waveform_pair(left, right, same_acquisition=True)

    assert relationship["channels"] == [1, 2]
    assert relationship["common_time"]["overlap"] is True
    assert relationship["frequency"]["ratio_high_over_low"] == 1.0
    assert 0.45 < relationship["voltage"]["vpp_ratio_right_over_left"] < 0.55
    assert 0.19 < relationship["voltage"]["mean_delta_right_minus_left_v"] < 0.21
    assert relationship["correlation"]["max_abs_cross_correlation"] > 0.9
    assert relationship["intersections"]["mode"] == "finite"
    assert relationship["intersections"]["count"] > 0
    assert relationship["phase_degrees_at_left_frequency"] is not None


def test_waveform_pair_skips_timing_analysis_when_not_same_acquisition():
    t = np.linspace(0.0, 0.009, 1000)
    left = _waveform(1, np.sin(2 * np.pi * 1000 * t), stop=float(t[-1]))
    right = _waveform(2, np.sin(2 * np.pi * 1000 * (t - 0.00025)), stop=float(t[-1]))

    relationship = analyze_waveform_pair(left, right, same_acquisition=False)

    assert relationship["common_time"]["same_acquisition"] is False
    assert relationship["common_time"]["overlap"] is None
    assert relationship["phase_degrees_at_left_frequency"] is None
    # 跨采集的波形没有共同时间基准，相关性和交点必须整段跳过而不是给出看似精确的数字
    assert relationship["correlation"] == {"status": "skipped", "reason": "not_same_acquisition"}
    assert relationship["intersections"] == {"status": "skipped", "reason": "not_same_acquisition"}
    assert "not_same_acquisition_timing_relationships_skipped" in relationship["warnings"]
    # 同步无关的量仍然保留
    assert relationship["frequency"]["ratio_high_over_low"] == 1.0


def test_waveform_pair_reports_phase_lag_in_degrees():
    t = np.linspace(0.0, 0.004, 4000)
    left = _waveform(1, np.sin(2 * np.pi * 1000 * t), stop=float(t[-1]))

    for expected_degrees in (0.0, 90.0, 180.0, 270.0):
        right = _waveform(
            2,
            np.sin(2 * np.pi * 1000 * t - np.deg2rad(expected_degrees)),
            stop=float(t[-1]),
        )

        relationship = analyze_waveform_pair(left, right, same_acquisition=True)

        assert relationship["phase_degrees_at_left_frequency"] == pytest.approx(
            expected_degrees, abs=0.5
        )


def test_waveform_pair_reports_180_degrees_for_inverted_signal():
    t = np.linspace(0.0, 0.004, 4000)
    left = np.sin(2 * np.pi * 1000 * t)

    relationship = analyze_waveform_pair(
        _waveform(1, left, stop=float(t[-1])),
        _waveform(2, -left, stop=float(t[-1])),
        same_acquisition=True,
    )

    # 用相关峰绝对值选 lag 会把它报成 0°
    assert relationship["phase_degrees_at_left_frequency"] == pytest.approx(180.0, abs=0.5)


def test_waveform_pair_phase_rejects_dc_leakage_in_noninteger_cycle_window():
    t = np.linspace(0.0, 0.0045, 4501)
    left = _waveform(1, np.sin(2 * np.pi * 1000 * t), stop=float(t[-1]))
    right = _waveform(
        2, np.sin(2 * np.pi * 1000 * t - np.pi / 2) + 5.0, stop=float(t[-1]),
    )

    relationship = analyze_waveform_pair(left, right, same_acquisition=True)

    assert relationship["frequency"]["left_hz"] == pytest.approx(1000.0, abs=0.1)
    assert relationship["frequency"]["right_hz"] == pytest.approx(1000.0, abs=0.1)
    assert relationship["phase_degrees_at_left_frequency"] == pytest.approx(90.0, abs=0.5)


def test_waveform_relationships_report_all_pairs_for_four_channels():
    t = np.linspace(0.0, 0.004, 500)
    waveforms = {
        channel: _waveform(channel, np.sin(2 * np.pi * 1000 * t + channel), stop=float(t[-1]))
        for channel in range(1, 5)
    }

    relationships = analyze_waveform_relationships(waveforms)

    assert len(relationships) == 6
    assert relationships[0]["channels"] == [1, 2]
    assert relationships[-1]["channels"] == [3, 4]


def test_waveform_pair_warns_when_frequency_confidence_is_low():
    t = np.linspace(0.0, 0.0005, 100)
    left = _waveform(1, np.sin(2 * np.pi * 1000 * t), stop=float(t[-1]))
    right = _waveform(2, np.sin(2 * np.pi * 2000 * t), stop=float(t[-1]))

    relationship = analyze_waveform_pair(left, right, same_acquisition=True)

    assert relationship["frequency"]["left_hz"] is None
    assert any("frequency_low_confidence" in warning for warning in relationship["warnings"])


def test_waveform_pair_reports_intersection_points():
    t = np.linspace(0.0, 1.0, 1001)
    left = _waveform(1, t - 0.25, stop=float(t[-1]))
    right = _waveform(2, np.zeros_like(t), stop=float(t[-1]))

    relationship = analyze_waveform_pair(left, right, same_acquisition=True)

    intersections = relationship["intersections"]
    assert intersections["mode"] == "finite"
    assert intersections["count"] == 1
    assert intersections["returned"] == 1
    assert intersections["truncated"] is False
    assert intersections["points"][0]["time_s"] == 0.25
    assert intersections["points"][0]["voltage_v"] == 0.0
    assert intersections["points"][0]["direction"] == "left_minus_right_rising"


def test_waveform_pair_can_truncate_many_intersections():
    t = np.linspace(0.0, 0.01, 2000)
    left = _waveform(1, np.sin(2 * np.pi * 1000 * t), stop=float(t[-1]))
    right = _waveform(2, np.zeros_like(t), stop=float(t[-1]))

    relationship = analyze_waveform_pair(left, right, same_acquisition=True, max_intersections=3)

    assert relationship["intersections"]["count"] > 3
    assert relationship["intersections"]["returned"] == 3
    assert relationship["intersections"]["truncated"] is True
    assert "intersections_truncated" in relationship["warnings"]


def test_waveform_pair_marks_coincident_waveforms_as_unbounded_intersections():
    t = np.linspace(0.0, 0.001, 100)
    values = np.sin(2 * np.pi * 1000 * t)

    relationship = analyze_waveform_pair(
        _waveform(1, values, stop=float(t[-1])),
        _waveform(2, values, stop=float(t[-1])),
        same_acquisition=True,
    )

    assert relationship["intersections"]["mode"] == "coincident"
    assert relationship["intersections"]["count"] is None
    assert "waveforms_coincident_intersections_unbounded" in relationship["warnings"]


def test_relationship_entry_points_default_to_unproven_timing():
    t = np.linspace(0.0, 0.009, 1000)
    waves = {
        1: _waveform(1, np.sin(2 * np.pi * 1000 * t)),
        2: _waveform(2, np.sin(2 * np.pi * 1000 * t - np.pi / 2)),
    }
    pair = analyze_waveform_pair(waves[1], waves[2])
    multiple = analyze_waveform_relationships(waves)
    for result in (pair, multiple[0]):
        assert result["common_time"]["same_acquisition"] is False
        assert result["phase_degrees_at_left_frequency"] is None
        assert result["correlation"]["status"] == "skipped"
        assert result["intersections"]["status"] == "skipped"
        assert result["frequency"]["ratio_high_over_low"] == 1.0


@pytest.mark.parametrize("assertion", ["false", 1, None])
def test_relationship_rejects_truthy_non_boolean_sync_assertions(assertion):
    wave = _waveform(1, np.zeros(10))
    with pytest.raises(ValueError, match="same_acquisition"):
        analyze_waveform_pair(wave, wave, same_acquisition=assertion)
