from tianyi_electronics_skill.core import analyze_samples, demo_waveform, load_waveform


def test_demo_frequency_and_levels():
    result = demo_waveform(sample_rate_hz=100_000, frequency_hz=1_000, seconds=0.01)
    assert abs(result["frequency_hz"] - 1_000) < 2
    assert 1.5 < result["peak_to_peak_v"] < 1.7
    assert result["rms_v"] > 0.5


def test_analyze_rejects_invalid_rate():
    try:
        analyze_samples([0, 1, 0, -1], 0)
    except ValueError as exc:
        assert "sample_rate" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_load_json_waveform(tmp_path):
    path = tmp_path / "wave.json"
    path.write_text('{"sample_rate_hz": 10, "samples": [0, 1, 0, -1]}', encoding="utf-8")
    samples, rate = load_waveform(path)
    assert samples == [0.0, 1.0, 0.0, -1.0]
    assert rate == 10
