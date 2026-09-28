import json
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import pytest

from wavebench.data.pipeline_operations import detect_peaks
from wavebench.data.signal_pipeline import fft_signal, welch_psd
from wavebench.errors import ConfigError
from wavebench.report.analysis import write_analysis_report
from wavebench.services.analysis_service import run_analysis
from wavebench.services.run_pipeline import ensure_operation_dependencies
from test_analysis_service import analysis_input as analysis_input
from test_psd_pipeline import PARAMS, plan_for, signal_for


PEAKS = dict(op="peaks", name="tones", polarity="positive", height=0,
             prominence=0, distance=1., width=0, max_peaks=100, metrics=["count"])


def test_plateaus_endpoints_ties_and_distance():
    pytest.importorskip("scipy")
    signal = signal_for([20, 0, 5, 5, 0, 5, 0, 2, 0, 20], fs=1)
    result = detect_peaks(signal, PEAKS)
    assert [peak["index"] for peak in result["peaks"]] == [2, 5, 7]
    assert result["peaks"][0]["width"] == pytest.approx(2)
    result = detect_peaks(signal, PEAKS | dict(distance=4))
    assert [peak["index"] for peak in result["peaks"]] == [2, 7]
    result = detect_peaks(signal, PEAKS | dict(max_peaks=1))
    assert result["count"] == 3 and result["retained_count"] == 1 and result["truncated"]


def test_time_polarity_and_physical_width():
    pytest.importorskip("scipy")
    signal = signal_for([0, 3, 0, -5, 0, 2, 0], fs=10)
    result = detect_peaks(signal, PEAKS | dict(polarity="both", distance=.1))
    assert [peak["value"] for peak in result["peaks"]] == [-5, 3, 2]
    assert result["axis_unit"] == "s" and result["value_unit"] == "V"
    assert result["peaks"][0]["position"] == pytest.approx(.3)
    negative = detect_peaks(signal, PEAKS | dict(polarity="negative", distance=.1))
    assert any(peak["value"] == -5 for peak in negative["peaks"])
    result = detect_peaks(signal, PEAKS | dict(distance=.1, width=.5))
    assert result["count"] == 0


def test_zero_height_disables_threshold_on_offset_signal():
    pytest.importorskip("scipy")
    signal = signal_for([-3, -1, -3, -4, -5], fs=1)
    result = detect_peaks(signal, PEAKS)
    assert result["peaks"][0]["value"] == -1
    assert detect_peaks(signal, PEAKS | dict(height=.01))["count"] == 0


def test_multitone_fft_and_psd():
    pytest.importorskip("scipy")
    axis = np.arange(128.) / 128
    signal = signal_for(np.sin(2*np.pi*8*axis) + .5*np.sin(2*np.pi*24*axis))
    fft = detect_peaks(fft_signal(signal), PEAKS | dict(height=.1, prominence=.1))
    assert [peak["position"] for peak in fft["peaks"]] == [8, 24]
    assert fft["value_unit"] == "V"
    psd = welch_psd(signal, **(PARAMS | dict(nperseg=128, noverlap=0, nfft=128)))
    result = detect_peaks(psd, PEAKS | dict(prominence=.01))
    assert [peak["position"] for peak in result["peaks"]] == [8, 24]
    assert result["value_unit"] == "V^2/Hz"
    assert detect_peaks(signal_for(np.zeros(30)), PEAKS)["count"] == 0


@pytest.mark.parametrize("change", [dict(name="bad/name"), dict(polarity="up"),
    dict(distance=0), dict(width=-1), dict(height=True), dict(prominence=float("inf")),
    dict(max_peaks=0), dict(max_peaks=10001), dict(max_peaks=2.5), dict(metrics=[]),
])
def test_peaks_strict_configuration(tmp_path, change):
    with pytest.raises(ConfigError):
        plan_for(tmp_path, [PEAKS | change])


def test_peaks_wrong_domain_and_duplicate_name(tmp_path):
    with pytest.raises(ConfigError, match="positive"):
        plan_for(tmp_path, [dict(op="fft"), PEAKS | dict(polarity="negative")])
    with pytest.raises(ConfigError, match="duplicate"):
        plan_for(tmp_path, [PEAKS, PEAKS])


def test_peak_artifacts_expectation_and_plot_markers(tmp_path, analysis_input):
    pytest.importorskip("scipy")
    capture, recipe = analysis_input
    axis = np.arange(128.) / 128
    np.save(capture / "ch1.npy", np.column_stack((axis, np.sin(2*np.pi*8*axis))))
    recipe.write_text('''schema="wavebench.analysis_recipe.v1"
operations=[
{op="fft"},
{op="peaks",name="tones",polarity="positive",height=0.1,prominence=0.1,distance=1,width=0,max_peaks=10,metrics=["count"]},
{op="export",name="spectrum",formats=["npy","csv"]},
]
[expect]
tones_count={min=1,max=1}
''')
    output = tmp_path / "analysis"
    result = run_analysis(capture, 1, recipe, output)
    assert result["status"] == "ok"
    assert result["artifact"]["metrics"]["tones_count"] == 1
    manifest = json.loads((output / "manifest.json").read_text())
    entry = manifest["peaks"][0]
    detected = json.loads((output / entry["json"]).read_text())
    assert detected["peaks"][0]["position"] == 8
    assert (output / entry["csv"]).read_text().splitlines()[0] == "index,position,value,prominence,width,polarity"
    html = write_analysis_report([output], tmp_path / "report.html").read_text()
    assert html.count('class="peak-marker"') == 1


def test_peak_dependency_preflight():
    with patch("wavebench.services.run_pipeline.import_module", return_value=SimpleNamespace()):
        with pytest.raises(ConfigError, match="find_peaks"):
            ensure_operation_dependencies([PEAKS])
