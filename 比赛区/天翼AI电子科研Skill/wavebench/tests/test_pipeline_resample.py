import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from wavebench.data.pipeline_operations import resample_signal
from wavebench.data.signal_pipeline import fft_signal, measure_frequency
from wavebench.errors import ConfigError, DataError
from wavebench.services.analysis_service import run_analysis
from wavebench.services.run_pipeline import ensure_operation_dependencies
from test_analysis_service import analysis_input as analysis_input
from test_psd_pipeline import plan_for, signal_for, EXPORT


RESAMPLE = dict(op="resample", up=1, down=2, window="kaiser", beta=5., padtype="line")


@pytest.mark.parametrize("up,down", [(2,1), (1,2), (3,2), (2,3), (1,1)])
def test_rates_lengths_constant_gain_and_time_origin(up, down):
    pytest.importorskip("scipy")
    signal = signal_for(np.ones(129) * 3, fs=128)
    signal.time_s[:] += 2
    result, metadata = resample_signal(signal, RESAMPLE | dict(up=up, down=down))
    assert len(result.time_s) == (129*up + down-1)//down
    assert result.time_s[0] == 2
    np.testing.assert_allclose(np.diff(result.time_s), down/up/128, rtol=1e-12)
    np.testing.assert_allclose(result.voltage_v, 3, atol=.003)
    assert metadata["sample_rate_hz"] == pytest.approx(128*up/down)
    assert len(metadata["filter_coefficients_sha256"]) == 64


def test_downsampling_suppresses_alias_and_fft_uses_new_rate():
    pytest.importorskip("scipy")
    times = np.arange(4096) / 1024
    values = np.sin(2*np.pi*32*times) + np.sin(2*np.pi*400*times)
    result, _ = resample_signal(signal_for(values, fs=1024), RESAMPLE)
    interior = result.voltage_v[50:-50]
    expected = np.sin(2*np.pi*32*result.time_s[50:-50])
    assert np.sqrt(np.mean((interior-expected)**2)) < .003
    fft = fft_signal(result)
    metrics, _ = measure_frequency(fft, ["peak_frequency_hz", "peak_amplitude_v"])
    assert metrics["peak_frequency_hz"] == pytest.approx(32)
    assert metrics["peak_amplitude_v"] == pytest.approx(1, abs=.005)


@pytest.mark.parametrize("change", [dict(up=0), dict(down=True), dict(up=1.5),
    dict(up=10001), dict(beta=-1), dict(beta=31), dict(window="hann"), dict(padtype="wrap")])
def test_resample_static_validation(tmp_path, change):
    with pytest.raises(ConfigError):
        plan_for(tmp_path, [RESAMPLE | change, EXPORT])


def test_normalization_domains_and_runtime_guards(tmp_path):
    plan = plan_for(tmp_path, [RESAMPLE | dict(up=20000, down=40000), EXPORT])
    assert plan.steps[1].fields["operations"][0]["down"] == 2
    for prefix in ([dict(op="fft")], [dict(op="window", name="hann")]):
        with pytest.raises(ConfigError):
            plan_for(tmp_path, [*prefix, RESAMPLE, EXPORT])
    signal = signal_for(np.ones(3000))
    with pytest.raises(DataError, match="20000000"):
        resample_signal(signal, RESAMPLE | dict(up=10000, down=1))
    signal.time_s[5] += .001
    with pytest.raises(DataError, match="uniform"):
        resample_signal(signal, RESAMPLE)


def test_output_sampling_metadata_and_later_filter_nyquist(tmp_path, analysis_input):
    pytest.importorskip("scipy")
    capture, recipe = analysis_input
    recipe.write_text('''schema="wavebench.analysis_recipe.v1"
operations=[
{op="resample",up=1,down=2,window="kaiser",beta=5,padtype="constant"},
{op="export",name="resampled",formats=["npy","csv"]},
{op="filter",family="fir",response="lowpass",cutoff_hz=40,numtaps=5,mode="causal"},
{op="measure",metrics=["voltage_rms_v"]},
]
''')
    original = (capture / "ch1.npy").read_bytes()
    output = tmp_path / "analysis"
    result = run_analysis(capture, 1, recipe, output)
    assert result["status"] == "failed"  # New Nyquist is 32 Hz, not 64 Hz.
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["sampling"]["samples"] == 64
    assert manifest["sampling"]["sample_rate_hz"] == 64
    assert manifest["transformations"][0]["input_sample_rate_hz"] == 128
    assert manifest["partial"]
    np.testing.assert_allclose(np.load(output / "exports/resampled.npy"),
                               np.loadtxt(output / "exports/resampled.csv", delimiter=",", skiprows=1))
    assert (capture / "ch1.npy").read_bytes() == original


def test_resample_missing_dependency():
    with patch("wavebench.services.run_pipeline.import_module", return_value=SimpleNamespace()):
        with pytest.raises(ConfigError, match="resample_poly"):
            ensure_operation_dependencies([RESAMPLE])


def test_resample_extreme_time_axis_and_short_result():
    pytest.importorskip("scipy")
    signal = signal_for(np.ones(5))
    signal.time_s[:] = np.arange(5) * 1e-309
    with pytest.raises(DataError, match="finite"):
        resample_signal(signal, RESAMPLE)
    result, _ = resample_signal(signal_for(np.ones(5)), RESAMPLE | dict(down=100))
    assert len(result.time_s) == 1


def test_runplan_and_offline_recipe_share_processed_results(tmp_path, analysis_input):
    pytest.importorskip("scipy")
    from pathlib import Path
    from wavebench.services.analysis_service import load_analysis_recipe
    from wavebench.services.run_pipeline import execute_analysis_pipeline
    from wavebench.services.run_artifacts import RunStepRecord
    from wavebench.services.run_plan import RunStep

    capture, _ = analysis_input
    recipe = Path("plans/example_processed_recipe.toml")
    fields = load_analysis_recipe(recipe)
    offline = run_analysis(capture, 1, recipe, tmp_path / "offline")
    source_step = RunStep(0, "scope.capture", {"save_npy": True}, "capture_main")
    source_record = RunStepRecord(index=0, kind="scope.capture", status="ok",
                                  fields=source_step.fields,
                                  artifact={"package": str(capture), "metadata": str(capture / "metadata.json")})
    step = RunStep(1, "analysis.pipeline", fields | {"source": {"step": "capture_main"}}, "processed")
    artifact = execute_analysis_pipeline(run_dir=tmp_path / "run", step=step,
                                          source_step=source_step, source_record=source_record)
    assert artifact["metrics"] == offline["artifact"]["metrics"]
    for first, second in zip(artifact["analysis_pipeline"]["exports"], offline["artifact"]["analysis_pipeline"]["exports"]):
        assert first["sha256"] == second["sha256"]


def test_psd_after_resample_uses_updated_rate():
    pytest.importorskip("scipy")
    from wavebench.data.signal_pipeline import welch_psd
    from test_psd_pipeline import PARAMS

    resampled, _ = resample_signal(signal_for(np.ones(128)), RESAMPLE)
    psd = welch_psd(resampled, **PARAMS)
    assert psd.frequency_hz[-1] == pytest.approx(32)
    assert psd.sample_interval_s == pytest.approx(1/64)
