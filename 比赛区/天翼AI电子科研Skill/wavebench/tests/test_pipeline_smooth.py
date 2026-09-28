import json
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import pytest

from wavebench.data.pipeline_operations import smooth_signal
from wavebench.errors import ConfigError, DataError
from wavebench.services.analysis_service import run_analysis
from wavebench.services.run_pipeline import ensure_operation_dependencies
from test_analysis_service import analysis_input as analysis_input
from test_psd_pipeline import plan_for, signal_for, EXPORT


SMOOTH = dict(op="smooth", method="moving_average", window_length=5, mode="centered", boundary="reflect")


def test_moving_average_boundary_and_causal_delay():
    signal = signal_for(np.arange(9.), fs=1)
    result, metadata = smooth_signal(signal, SMOOTH)
    np.testing.assert_allclose(result.voltage_v, [1.2, 1.4, 2, 3, 4, 5, 6, 6.6, 6.8])
    np.testing.assert_array_equal(result.time_s, signal.time_s)
    assert metadata["boundary_left_samples"] == metadata["boundary_right_samples"] == 2
    causal, metadata = smooth_signal(signal, SMOOTH | dict(mode="causal", boundary="edge"))
    np.testing.assert_allclose(causal.voltage_v, [0, .2, .6, 1.2, 2, 3, 4, 5, 6])
    assert metadata["nominal_group_delay_s"] == 2


@pytest.mark.parametrize("mode,boundary", [("centered", "reflect"), ("centered", "edge"), ("causal", "edge")])
@pytest.mark.parametrize("method", ["moving_average", "savgol"])
def test_smooth_preserves_constants_and_time(method, mode, boundary):
    if method == "savgol":
        pytest.importorskip("scipy")
    operation = SMOOTH | dict(method=method, mode=mode, boundary=boundary)
    if method == "savgol":
        operation["polyorder"] = 2
    signal = signal_for(np.ones(32) * 3)
    result, metadata = smooth_signal(signal, operation)
    np.testing.assert_allclose(result.voltage_v, 3, atol=1e-12)
    np.testing.assert_array_equal(result.time_s, signal.time_s)
    assert len(metadata["coefficients_sha256"]) == 64


@pytest.mark.parametrize("mode", ["centered", "causal"])
def test_savgol_preserves_quadratic_away_from_edges(mode):
    pytest.importorskip("scipy")
    signal = signal_for(np.arange(30.) ** 2)
    operation = SMOOTH | dict(method="savgol", polyorder=2, mode=mode, boundary="edge")
    result, _ = smooth_signal(signal, operation)
    region = slice(4, None) if mode == "causal" else slice(2, -2)
    np.testing.assert_allclose(result.voltage_v[region], signal.voltage_v[region], atol=1e-10)
    future = signal_for(np.r_[np.arange(15.) ** 2, np.ones(15) * 10000])
    if mode == "causal":
        altered, _ = smooth_signal(future, operation)
        np.testing.assert_array_equal(result.voltage_v[:15], altered.voltage_v[:15])


@pytest.mark.parametrize("change", [dict(method="median"), dict(window_length=4),
    dict(window_length=True), dict(window_length=1003), dict(polyorder=2),
    dict(mode="causal", boundary="reflect"), dict(boundary="wrap"),
    dict(method="savgol"), dict(method="savgol", polyorder=5),
])
def test_smooth_configuration(tmp_path, change):
    with pytest.raises(ConfigError):
        plan_for(tmp_path, [SMOOTH | change, EXPORT])


def test_smooth_domains_and_runtime_bounds(tmp_path):
    for prefix in ([dict(op="fft")], [dict(op="window", name="hann")]):
        with pytest.raises(ConfigError):
            plan_for(tmp_path, [*prefix, SMOOTH, EXPORT])
    with pytest.raises(DataError, match="window_length"):
        smooth_signal(signal_for(np.ones(4)), SMOOTH)
    signal = signal_for(np.ones(10))
    signal.time_s[5] += .001
    with pytest.raises(DataError, match="uniform"):
        smooth_signal(signal, SMOOTH)
    signal = signal_for(np.ones(5))
    signal.time_s[:] = np.arange(5) * 1e-309
    with pytest.raises(DataError, match="finite sample rate"):
        smooth_signal(signal, SMOOTH)


def test_smooth_dependency_only_for_savgol():
    with patch("wavebench.services.run_pipeline.import_module", return_value=SimpleNamespace()) as imported:
        ensure_operation_dependencies([SMOOTH])
        imported.assert_not_called()
        with pytest.raises(ConfigError, match="savgol_coeffs"):
            ensure_operation_dependencies([SMOOTH | dict(method="savgol", polyorder=2)])


def test_smooth_manifest_and_partial_exports(tmp_path, analysis_input):
    capture, recipe = analysis_input
    recipe.write_text('''schema="wavebench.analysis_recipe.v1"
operations=[
{op="smooth",method="moving_average",window_length=5,mode="causal",boundary="edge"},
{op="export",name="smoothed",formats=["npy"]},
{op="smooth",method="moving_average",window_length=501,mode="centered",boundary="reflect"},
{op="measure",metrics=["voltage_rms_v"]},
]
''')
    output = tmp_path / "analysis"
    before = (capture / "ch1.npy").read_bytes()
    result = run_analysis(capture, 1, recipe, output)
    assert result["status"] == "failed"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["partial"]
    assert manifest["transformations"][0]["nominal_group_delay_samples"] == 2
    assert (output / "exports/smoothed.npy").is_file()
    assert (capture / "ch1.npy").read_bytes() == before
