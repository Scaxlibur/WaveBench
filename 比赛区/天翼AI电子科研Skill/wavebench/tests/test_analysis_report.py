import json

import numpy as np
import pytest

from wavebench.errors import ConfigError
from wavebench.report.analysis import display_samples, write_analysis_report
from wavebench.services.analysis_service import run_analysis
from test_analysis_service import analysis_input as analysis_input


def test_comparison_uses_saved_exports_and_preserves_metrics(tmp_path, analysis_input):
    capture, recipe = analysis_input
    first, second = tmp_path / "first", tmp_path / "second"
    run_analysis(capture, 1, recipe, first)
    run_analysis(capture, 1, recipe, second)
    original = (first / "metrics.json").read_bytes()
    html = write_analysis_report([first, second], tmp_path / "compare.html").read_text()
    assert html.count("<svg") == 1
    assert html.count("<polyline") == 2  # NPY + CSV aren't drawn twice.
    assert "time_s / V" in html
    assert "voltage_mean_v" in html
    assert "sampling=" in html
    assert (first / "metrics.json").read_bytes() == original
    with pytest.raises(ConfigError, match="new file"):
        write_analysis_report([first], tmp_path / "compare.html")
    with pytest.raises(ConfigError, match="source capture"):
        write_analysis_report([first], capture / "report.html")
    (first / "manifest.json").write_text("invalid json")
    with pytest.raises(ConfigError, match="source capture"):
        write_analysis_report([first], capture / "report.html")


def test_bad_exports_fall_back_to_csv_and_show_warning(tmp_path, analysis_input):
    capture, recipe = analysis_input
    output = tmp_path / "analysis"
    run_analysis(capture, 1, recipe, output)
    (output / "exports/waveform.npy").write_bytes(b"corrupt")
    html = write_analysis_report([output], tmp_path / "report.html").read_text()
    assert "SHA-256 mismatch" in html
    assert html.count("<polyline") == 1
    (output / "exports/waveform.csv").unlink()
    html = write_analysis_report([output], tmp_path / "report2.html").read_text()
    assert "No usable curve exports" in html


def test_distinct_sources_are_not_overlaid(tmp_path, analysis_input):
    capture, recipe = analysis_input
    first, second = tmp_path / "first", tmp_path / "second"
    run_analysis(capture, 1, recipe, first)
    np.save(capture / "ch1.npy", np.column_stack((np.arange(128.) / 128, np.zeros(128))))
    run_analysis(capture, 1, recipe, second)
    html = write_analysis_report([first, second], tmp_path / "report.html").read_text()
    assert html.count("<svg") == 2


def test_fft_and_psd_have_separate_units(tmp_path, analysis_input):
    pytest.importorskip("scipy")
    capture, recipe = analysis_input
    roots = []
    for name, operation in [("fft", '{op="fft"}'), ("psd", '{op="psd",method="welch",window="hann",nperseg=32,noverlap=16,nfft=32,detrend="none",average="mean"}')]:
        recipe.write_text('schema="wavebench.analysis_recipe.v1"\noperations=[' + operation + ',{op="export",name="spectrum",formats=["npy"]}]')
        roots.append(tmp_path / name)
        run_analysis(capture, 1, recipe, roots[-1])
    html = write_analysis_report(roots, tmp_path / "report.html").read_text()
    assert html.count("<svg") == 2
    assert "frequency_hz / V²/Hz" in html
    assert "frequency_hz / V" in html


def test_display_sampling_retains_narrow_peak():
    data = np.column_stack((np.arange(20000.), np.zeros(20000)))
    data[9123, 1] = 100
    sampled = display_samples(data)
    assert len(sampled) <= 1200
    assert sampled[:, 1].max() == 100
    assert sampled[0, 0] == 0 and sampled[-1, 0] == 19999


def test_export_path_escape_is_not_read(tmp_path, analysis_input):
    capture, recipe = analysis_input
    output = tmp_path / "analysis"
    run_analysis(capture, 1, recipe, output)
    manifest = json.loads((output / "manifest.json").read_text())
    for item in manifest["exports"]:
        item["path"] = "../capture/ch1.npy"
    (output / "manifest.json").write_text(json.dumps(manifest))
    html = write_analysis_report([output], tmp_path / "report.html").read_text()
    assert "artifact path must be relative" in html
    assert "<polyline" not in html
