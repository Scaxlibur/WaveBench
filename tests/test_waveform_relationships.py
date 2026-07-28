import numpy as np

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

    relationship = analyze_waveform_pair(left, right)

    assert relationship["channels"] == [1, 2]
    assert relationship["common_time"]["overlap"] is True
    assert relationship["frequency"]["ratio_high_over_low"] == 1.0
    assert 0.45 < relationship["voltage"]["vpp_ratio_right_over_left"] < 0.55
    assert 0.19 < relationship["voltage"]["mean_delta_right_minus_left_v"] < 0.21
    assert relationship["correlation"]["max_abs_cross_correlation"] > 0.9
    assert relationship["intersections"]["mode"] == "finite"
    assert relationship["intersections"]["count"] > 0
    assert relationship["phase_degrees_at_left_frequency"] is not None


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

    relationship = analyze_waveform_pair(left, right)

    assert relationship["frequency"]["left_hz"] is None
    assert any("frequency_low_confidence" in warning for warning in relationship["warnings"])


def test_waveform_pair_reports_intersection_points():
    t = np.linspace(0.0, 1.0, 1001)
    left = _waveform(1, t - 0.25, stop=float(t[-1]))
    right = _waveform(2, np.zeros_like(t), stop=float(t[-1]))

    relationship = analyze_waveform_pair(left, right)

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

    relationship = analyze_waveform_pair(left, right, max_intersections=3)

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
    )

    assert relationship["intersections"]["mode"] == "coincident"
    assert relationship["intersections"]["count"] is None
    assert "waveforms_coincident_intersections_unbounded" in relationship["warnings"]
