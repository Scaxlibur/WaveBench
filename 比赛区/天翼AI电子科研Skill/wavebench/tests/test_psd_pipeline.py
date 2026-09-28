from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from wavebench.data.packages import load_run_package
from wavebench.data.signal_pipeline import validate_waveform, welch_psd, window_signal
from wavebench.errors import ConfigError, DataError
from wavebench.logging import CommandLogger
from wavebench.report.html import write_run_report_html
from wavebench.services.execution_intent import build_execution_intent
from wavebench.services.run_pipeline import (
    ensure_analysis_pipeline_dependencies,
    execute_analysis_pipeline,
)
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunService

import test_run_pipeline as artifact_helpers
from test_run_service import make_config


PARAMS = dict(method="welch", window="hann", nperseg=16, noverlap=8,
              nfft=16, detrend="none", average="mean")
EXPORT = dict(op="export", name="density", formats=["npy", "csv"])


def plan_for(tmp_path, operations):
    tables = ["{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in op.items()) + " }"
              for op in operations]
    path = tmp_path / "plan.toml"
    path.write_text('''[[steps]]
id = "capture_main"
kind = "scope.capture"
save_npy = true
[[steps]]
id = "density_main"
kind = "analysis.pipeline"
source = { step = "capture_main" }
operations = [''' + ", ".join(tables) + "]\n", encoding="utf-8")
    return load_run_plan(path)


def signal_for(voltage, fs=128):
    return validate_waveform(np.column_stack((np.arange(len(voltage)) / fs, voltage)))


@pytest.mark.parametrize("field,value", [
    ("method", "periodogram"), ("window", "boxcar"), ("detrend", False),
    ("average", "sum"), ("nperseg", 3), ("nperseg", 16.0), ("nfft", True),
    ("noverlap", -1), ("noverlap", 16), ("nfft", 15), ("nfft", "16"),
])
def test_psd_invalid_parameters_rejected_offline(tmp_path, field, value):
    with pytest.raises(ConfigError):
        plan_for(tmp_path, [dict(op="psd", **(PARAMS | {field: value})), EXPORT])


@pytest.mark.parametrize("field", list(PARAMS))
def test_psd_requires_every_parameter(tmp_path, field):
    params = PARAMS.copy()
    del params[field]
    with pytest.raises(ConfigError, match="missing required"):
        plan_for(tmp_path, [dict(op="psd", **params), EXPORT])


@pytest.mark.parametrize("operations", [
    [dict(op="psd", **PARAMS, scaling="spectrum"), EXPORT],
    [dict(op="window", name="hann"), dict(op="psd", **PARAMS), EXPORT],
    [dict(op="fft"), dict(op="psd", **PARAMS), EXPORT],
    [dict(op="psd", **PARAMS), dict(op="fft"), EXPORT],
    [dict(op="psd", **PARAMS), dict(op="psd", **PARAMS), EXPORT],
    [dict(op="psd", **PARAMS), dict(op="measure", metrics=["peak_amplitude_v"])],
    [dict(op="psd", **PARAMS), dict(op="remove_dc"), EXPORT],
    [dict(op="measure", metrics=["voltage_rms_v"]), dict(op="psd", **PARAMS)],
    [dict(op="export", name="before", formats=["npy"]), dict(op="psd", **PARAMS)],
])
def test_psd_domain_and_result_rules(tmp_path, operations):
    with pytest.raises(ConfigError):
        plan_for(tmp_path, operations)


def test_psd_normalization_and_offline_intent(tmp_path):
    plan = plan_for(tmp_path, [
        dict(op="measure", metrics=["voltage_rms_v"]), dict(op="remove_dc"),
        dict(op="psd", **(PARAMS | dict(window=" HANN ", average="MEDIAN"))), EXPORT,
    ])
    intent = build_execution_intent(plan, make_config(str(tmp_path)))
    operation = intent.operations[1]
    assert operation["instrument_kind"] is None
    assert operation["effect"] == "offline"
    assert operation["lease_mode"] == "none"
    assert operation["parameters"]["operations"][2] == dict(
        op="psd", **(PARAMS | dict(average="median"))
    )


@pytest.mark.parametrize("window", ["hann", "hamming", "blackman"])
@pytest.mark.parametrize("nfft", [16, 17, 32])
@pytest.mark.parametrize("average", ["mean", "median"])
def test_psd_matches_independent_segment_periodograms(window, nfft, average):
    pytest.importorskip("scipy")
    values = np.random.default_rng(81).normal(size=43)
    values[18] += 15  # Median must differ materially from mean.
    params = PARAMS | dict(window=window, nfft=nfft, average=average)
    result = welch_psd(signal_for(values), **params)
    weights = {"hann": np.hanning, "hamming": np.hamming, "blackman": np.blackman}[window](17)[:-1]
    segments = np.array([values[start:start + 16] for start in (0, 8, 16, 24)])
    spectra = np.abs(np.fft.rfft(segments * weights, n=nfft)) ** 2
    spectra /= 128 * np.sum(weights ** 2)
    spectra[:, 1:(-1 if nfft % 2 == 0 else None)] *= 2
    # Four segments: SciPy's median bias correction is 1 + 1/3 - 1/2 = 5/6.
    expected = spectra.mean(axis=0) if average == "mean" else np.median(spectra, axis=0) / (5 / 6)
    np.testing.assert_allclose(result.psd_v2_per_hz, expected, rtol=2e-13, atol=1e-15)
    np.testing.assert_allclose(result.frequency_hz, np.arange(nfft // 2 + 1) * 128 / nfft)
    assert result.segment_count == 4
    assert result.discarded_tail_samples == 3
    assert result.window_power_gain == pytest.approx(np.mean(weights ** 2))


@pytest.mark.parametrize("nfft", [16, 17, 64])
@pytest.mark.parametrize("values", [np.ones(16) * 3, (-1.) ** np.arange(16) * 3])
def test_psd_dc_nyquist_and_zero_padding_conserve_power(nfft, values):
    pytest.importorskip("scipy")
    result = welch_psd(signal_for(values), **(PARAMS | dict(nfft=nfft)))
    assert np.sum(result.psd_v2_per_hz) * 128 / nfft == pytest.approx(9.)
    if nfft == 16:
        index = 0 if values[1] > 0 else 8
        assert result.psd_v2_per_hz[index] == pytest.approx(0.75)


@pytest.mark.parametrize("detrend", ["constant", "linear"])
def test_psd_detrend_is_per_segment(detrend):
    pytest.importorskip("scipy")
    values = np.arange(40.) * 0.3 + np.sin(np.arange(40.))
    result = welch_psd(signal_for(values), **(PARAMS | dict(detrend=detrend)))
    weights = np.hanning(17)[:-1]
    segments = np.array([values[i:i + 16] for i in (0, 8, 16, 24)])
    segments -= segments.mean(axis=1, keepdims=True)
    if detrend == "linear":
        axis = np.arange(16.) - 7.5
        segments -= np.outer(segments @ axis / (axis @ axis), axis)
    expected = np.mean(abs(np.fft.rfft(segments * weights)) ** 2, axis=0)
    expected /= 128 * np.sum(weights ** 2)
    expected[1:-1] *= 2
    np.testing.assert_allclose(result.psd_v2_per_hz, expected, atol=1e-16)


def test_psd_rejects_short_nonuniform_and_windowed_input():
    with pytest.raises(DataError, match="nperseg"):
        welch_psd(signal_for(np.ones(15)), **PARAMS)
    signal = signal_for(np.ones(16))
    with pytest.raises(DataError, match="whole-signal window"):
        welch_psd(window_signal(signal, "hann"), **PARAMS)
    signal.time_s[5] += 0.001
    with pytest.raises(DataError, match="uniformly sampled"):
        welch_psd(signal, **PARAMS)


def test_psd_artifacts_report_and_source_immutability(tmp_path):
    pytest.importorskip("scipy")
    helper = artifact_helpers.AnalysisPipelineArtifactTests()
    source, record, raw = helper.source(tmp_path, signal_for(np.ones(19)).as_array())
    original = raw.read_bytes()
    plan = plan_for(tmp_path, [dict(op="psd", **PARAMS), EXPORT])
    artifact = execute_analysis_pipeline(run_dir=tmp_path, step=plan.steps[1],
                                         source_step=source, source_record=record)
    pipeline = artifact["analysis_pipeline"]
    assert pipeline["status"] == "ok"
    manifest = json.loads((tmp_path / pipeline["manifest"]).read_text())
    assert manifest["stages"][1]["output_domain"] == "psd"
    assert manifest["psd"]["segment_count"] == 1
    assert manifest["psd"]["discarded_tail_samples"] == 3
    assert manifest["psd"]["window_periodic"] is True
    assert len(manifest["psd"]["window_sha256"]) == 64
    assert len(pipeline["warnings"]) == 2
    npy, csv = [tmp_path / item["path"] for item in pipeline["exports"]]
    np.testing.assert_allclose(np.load(npy), np.loadtxt(csv, delimiter=",", skiprows=1))
    assert csv.read_text().splitlines()[0] == "frequency_hz,psd_v2_per_hz"
    for item in pipeline["exports"]:
        assert item["columns"] == ["frequency_hz", "psd_v2_per_hz"]
        assert item["sha256"] == sha256((tmp_path / item["path"]).read_bytes()).hexdigest()
        assert item["path"].startswith("processing/01_density_main/exports/")
    assert raw.read_bytes() == original
    assert artifact["metrics"] == {}
    (tmp_path / "run.json").write_text(json.dumps(dict(status="ok", steps=[dict(
        index=1, id="density_main", kind="analysis.pipeline", status="ok", artifact=artifact
    )])))
    html = write_run_report_html(load_run_package(tmp_path)).read_text()
    assert "psd(method=welch, window=hann, nperseg=16, noverlap=8" in html
    assert 'href="processing/01_density_main/exports/density.csv"' in html
    assert "PSD discarded 3 trailing samples" in html


def test_failed_psd_preserves_earlier_export(tmp_path):
    helper = artifact_helpers.AnalysisPipelineArtifactTests()
    source, record, _ = helper.source(tmp_path, signal_for(np.ones(8)).as_array())
    before = dict(op="export", name="before", formats=["npy"])
    plan = plan_for(tmp_path, [before, dict(op="psd", **PARAMS), EXPORT])
    artifact = execute_analysis_pipeline(run_dir=tmp_path, step=plan.steps[1],
                                         source_step=source, source_record=record)
    pipeline = artifact["analysis_pipeline"]
    manifest = json.loads((tmp_path / pipeline["manifest"]).read_text())
    assert manifest["partial"] is True
    assert manifest["failed_stage"] == "operations[1]"
    assert "psd" not in manifest
    assert manifest["stages"][-1]["status"] == "skipped"
    assert (tmp_path / pipeline["exports"][0]["path"]).is_file()


@pytest.mark.parametrize("missing", ["welch", "get_window", "module"])
def test_psd_dependency_checked_before_hardware(tmp_path, missing):
    plan = plan_for(tmp_path, [dict(op="psd", **PARAMS), EXPORT])
    service = RunService(config=make_config(str(tmp_path)), logger=CommandLogger())
    available = {name: lambda: None for name in ("welch", "get_window") if name != missing}
    with patch("wavebench.services.run_pipeline.import_module",
               side_effect=ImportError if missing == "module" else None,
               return_value=SimpleNamespace(**available)), \
         patch.object(service, "_run_instrument_services") as hardware:
        with pytest.raises(ConfigError, match=r"\.\[analysis\]"):
            service.run(plan)
    hardware.assert_not_called()


def test_numpy_only_does_not_require_scipy(tmp_path):
    plan = plan_for(tmp_path, [EXPORT])
    with patch("wavebench.services.run_pipeline.import_module") as imported:
        ensure_analysis_pipeline_dependencies(plan)
    imported.assert_not_called()
