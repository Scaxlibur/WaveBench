import json

import numpy as np
import pytest

from wavebench.data.pipeline_operations import measure_band
from wavebench.data.signal_pipeline import PsdSignal, welch_psd
from wavebench.errors import ConfigError, DataError
from wavebench.services.analysis_service import run_analysis
from test_analysis_service import analysis_input as analysis_input
from test_psd_pipeline import PARAMS, plan_for, signal_for


BAND = dict(op="measure_band", name="audio", band_hz=[0, 64], exclude_hz=[],
            metrics=["mean_square_v2", "rms_v"])


def density():
    return PsdSignal(np.arange(5.) * 16, np.ones(5), 1/128, 8,
                     {"average": "mean"}, 1, 0, 1, "", "")


def test_closed_bin_integration_and_exclusion():
    values, metadata, warnings = measure_band(density(), BAND)
    assert values["audio_mean_square_v2"] == 80  # Both endpoints have full bin weight.
    assert values["audio_rms_v"] == pytest.approx(np.sqrt(80))
    assert metadata["selected_bins"] == 5 and warnings == []
    values, metadata, _ = measure_band(density(), BAND | dict(exclude_hz=[[16, 32]], metrics=["noise_rms_v"]))
    assert values["audio_noise_rms_v"] == pytest.approx(np.sqrt(48))
    assert metadata["selected_bins"] == 3


def test_empty_band_and_nyquist_boundary():
    values, _, warnings = measure_band(density(), BAND | dict(band_hz=[1, 2]))
    assert all(value is None for value in values.values()) and warnings
    with pytest.raises(DataError, match="Nyquist"):
        measure_band(density(), BAND | dict(band_hz=[0, 65]))


@pytest.mark.parametrize("nfft", [16, 17, 64])
def test_integrated_psd_power_and_zero_padding(nfft):
    pytest.importorskip("scipy")
    values = np.cos(np.arange(16) * np.pi) * 3
    signal = welch_psd(signal_for(values), **(PARAMS | dict(nfft=nfft)))
    measured, _, _ = measure_band(signal, BAND)
    assert measured["audio_mean_square_v2"] == pytest.approx(9)
    assert measured["audio_rms_v"] == pytest.approx(3)


@pytest.mark.parametrize("change", [
    dict(name="../bad"), dict(band_hz=[2, 1]), dict(band_hz=[True, 5]),
    dict(exclude_hz=[[0, 65]]), dict(exclude_hz="none"), dict(metrics=["noise_rms_v"]),
    dict(metrics=["rms_v", "rms_v"]), dict(metrics=["power_w"]),
])
def test_band_static_validation(tmp_path, change):
    with pytest.raises(ConfigError):
        plan_for(tmp_path, [dict(op="psd", **PARAMS), BAND | change])


def test_band_requires_psd_and_unique_name(tmp_path):
    with pytest.raises(ConfigError, match="requires PSD"):
        plan_for(tmp_path, [BAND])
    with pytest.raises(ConfigError, match="duplicate"):
        plan_for(tmp_path, [dict(op="psd", **PARAMS), BAND,
                            BAND | dict(metrics=["noise_rms_v"], exclude_hz=[[0, 1]])])


def test_band_expectation_and_stage_metadata(tmp_path, analysis_input):
    pytest.importorskip("scipy")
    capture, recipe = analysis_input
    recipe.write_text('''schema="wavebench.analysis_recipe.v1"
operations=[
{op="psd",method="welch",window="hann",nperseg=16,noverlap=8,nfft=16,detrend="none",average="mean"},
{op="measure_band",name="all",band_hz=[0,64],exclude_hz=[],metrics=["rms_v"]},
]
[expect]
all_rms_v={min=0.99,max=1.01}
''')
    output = tmp_path / "analysis"
    result = run_analysis(capture, 1, recipe, output)
    assert result["status"] == "ok"
    assert result["artifact"]["metrics"]["all_rms_v"] == pytest.approx(1)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["stages"][2]["measurement"]["selected_bins"] == 9
    assert manifest["stages"][2]["output_domain"] == "psd"
