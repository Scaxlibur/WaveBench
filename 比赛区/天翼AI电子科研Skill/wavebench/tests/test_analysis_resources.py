import csv
from dataclasses import replace
from hashlib import sha256
import io
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import pytest

from wavebench.data.analysis_resources import (
    AnalysisLimits, AnalysisBudget, AnalysisResourceError, check_static, load_resource_limits,
)
from wavebench.data.analysis_io import load_waveform, mapped_npy, BLOCK_ROWS
from wavebench.data.signal_pipeline import TimeSignal, validate_waveform
from wavebench.errors import ConfigError, DataError, ExecutionIntentError, error_envelope
from wavebench.services.analysis_service import run_analysis, check_analysis
from wavebench.services.execution_intent import build_execution_intent, verify_execution_intent
from wavebench.services.run_pipeline import _atomic_write_csv, _atomic_write_npy, _atomic_write_bytes, _export_signal
from wavebench.report.analysis import read_curve, write_analysis_report
from test_analysis_service import analysis_input as analysis_input
from test_psd_pipeline import PARAMS, plan_for, EXPORT
from test_run_service import make_config


def test_limit_config_and_tightening(tmp_path):
    limits = AnalysisLimits()
    for values in ({"bad": 1}, {"max_fft_length": True}, {"max_fft_length": 0}, {"max_fft_length": 1.5}):
        with pytest.raises(ConfigError):
            limits.tighten(values)
    with pytest.raises(ConfigError, match="cannot exceed"):
        limits.tighten({"max_fft_length": 2**21})
    assert limits.tighten({"max_fft_length": 64}).max_fft_length == 64
    profile = tmp_path / "resources.toml"
    profile.write_text('schema="wavebench.analysis_resources.v1"\nmax_fft_length=2097152\n')
    assert load_resource_limits(profile).max_fft_length == 2**21


def test_static_and_joint_welch_budget():
    limits = AnalysisLimits()
    with pytest.raises(AnalysisResourceError, match="max_zero_phase_fir_taps"):
        check_static([dict(op="filter", family="fir", mode="zero_phase", numtaps=257)], limits)
    operation = dict(op="psd", **(PARAMS | dict(nfft=2**20, nperseg=1024, noverlap=512, average="median")))
    check_static([operation, EXPORT], limits)
    with pytest.raises(AnalysisResourceError) as caught:
        AnalysisBudget(limits).stage(operation, 2**20)
    payload = error_envelope(caught.value)
    assert payload["code"] == "resource_limit_exceeded"
    assert payload["details"]["dimension"] == "max_working_bytes"
    assert payload["details"]["requested"] > 8 * 1024**3


def test_cumulative_work_and_export_budget():
    budget = AnalysisBudget(replace(AnalysisLimits(), max_work_units=150))
    budget.stage({"op": "remove_dc"}, 100)
    with pytest.raises(AnalysisResourceError, match="max_work_units"):
        budget.stage({"op": "remove_dc"}, 100)
    budget = AnalysisBudget(replace(AnalysisLimits(), max_output_bytes=4000))
    budget.output_bytes = 2000
    with pytest.raises(AnalysisResourceError, match="max_output_bytes"):
        budget.stage(EXPORT, 100)


def test_header_rejects_huge_claim_before_mapping(tmp_path):
    path = tmp_path / "huge.npy"
    with path.open("wb") as file:
        np.lib.format.write_array_header_1_0(file, dict(descr="<f8", fortran_order=False, shape=(2**35, 2)))
    with patch("numpy.memmap", side_effect=AssertionError("must reject before mmap")):
        with pytest.raises(AnalysisResourceError, match="max_input_samples"):
            load_waveform(path, AnalysisLimits())


@pytest.mark.parametrize("kind", ["truncated", "object", "complex", "shape"])
def test_invalid_npy_rejected_before_mapping(tmp_path, kind):
    path = tmp_path / "bad.npy"
    data = np.ones((10, 2))
    if kind == "object":
        data = data.astype(object)
    elif kind == "complex":
        data = data.astype(complex)
    elif kind == "shape":
        data = np.ones((10, 3))
    np.save(path, data)
    if kind == "truncated":
        path.write_bytes(path.read_bytes()[:-1])
    with patch("numpy.memmap", side_effect=AssertionError("must reject before mmap")):
        with pytest.raises(DataError):
            load_waveform(path, AnalysisLimits())


@pytest.mark.parametrize("order", ["C", "F"])
@pytest.mark.parametrize("dtype", ["<f8", ">f8", "<f4", "<i4"])
def test_mapped_source_preserves_values_and_raw_hash(tmp_path, order, dtype):
    path = tmp_path / "source.npy"
    array = np.array(np.column_stack((np.arange(100), np.arange(100)**2)), dtype=dtype, order=order)
    np.save(path, array)
    raw = path.read_bytes()
    source, digest = load_waveform(path, AnalysisLimits())
    np.testing.assert_array_equal(source.as_array(), array)
    source.voltage_v[:] = 0
    assert path.read_bytes() == raw
    assert digest == sha256(raw).hexdigest()


def test_cross_block_axis_and_finite_validation():
    array = np.column_stack((np.arange(BLOCK_ROWS + 2.), np.zeros(BLOCK_ROWS + 2)))
    array[BLOCK_ROWS, 0] = array[BLOCK_ROWS-1, 0]
    with pytest.raises(DataError, match="increasing"):
        validate_waveform(array)
    array[BLOCK_ROWS, 0] += 1
    array[-1, 1] = np.inf
    with pytest.raises(DataError, match="finite"):
        validate_waveform(array)


def test_source_change_detected_and_mapping_closed(tmp_path):
    path = tmp_path / "source.npy"
    np.save(path, np.column_stack((np.arange(10.), np.ones(10))))
    with pytest.raises(DataError, match="changed"):
        with mapped_npy(path, AnalysisLimits(), columns=2, source=True) as (array, _):
            with path.open("ab") as file:
                file.write(b"x")
    assert array._mmap.closed


def test_streamed_exports_are_byte_compatible(tmp_path):
    data = np.column_stack((np.arange(10001.) / 128, np.sin(np.arange(10001.))))
    signal = TimeSignal(data[:, 0], data[:, 1])
    budget = AnalysisBudget(AnalysisLimits())
    with patch.object(TimeSignal, "as_array", side_effect=AssertionError("no full export matrix")):
        exported = list(_export_signal(run_dir=tmp_path, processing_dir=tmp_path, signal=signal,
                                      name="wave", formats=["npy", "csv"], budget=budget))
    npy = io.BytesIO()
    np.save(npy, data, allow_pickle=False)
    csv_file = io.StringIO(newline="")
    writer = csv.writer(csv_file)
    writer.writerow(["time_s", "voltage_v"])
    writer.writerows(data.tolist())
    assert (tmp_path / exported[0]["path"]).read_bytes() == npy.getvalue()
    assert (tmp_path / exported[1]["path"]).read_bytes() == csv_file.getvalue().encode()
    assert budget.output_bytes == len(npy.getvalue()) + len(csv_file.getvalue().encode())


def test_write_limit_and_io_failure_cleanup(tmp_path):
    path = tmp_path / "result.csv"
    path.write_text("keep original")
    with pytest.raises(AnalysisResourceError):
        _atomic_write_csv(path, ["x", "y"], np.ones((100, 2)),
                          budget=AnalysisBudget(replace(AnalysisLimits(), max_output_bytes=10)))
    assert path.read_text() == "keep original"
    with patch("wavebench.services.run_pipeline._replace_file", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            _atomic_write_csv(path, ["x", "y"], np.ones((2, 2)))
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("kind", ["bytes", "npy", "csv"])
@pytest.mark.parametrize("persistent", [False, True])
def test_analysis_windows_replace_contention(tmp_path, monkeypatch, kind, persistent):
    from unittest.mock import Mock
    from wavebench.services import platform_io

    path = tmp_path / "result"
    path.write_bytes(b"keep original")
    real_replace = platform_io.os.replace
    attempts = []

    def move(source, target, flags):
        attempts.append((source, target))
        assert path.read_bytes() == b"keep original"
        if persistent or len(attempts) == 1:
            return 0
        real_replace(source, target)
        return 1

    kernel32 = SimpleNamespace(MoveFileExW=Mock(side_effect=move))
    monkeypatch.setattr(platform_io, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(platform_io.ctypes, "WinDLL", lambda *a, **kw: kernel32, raising=False)
    monkeypatch.setattr(platform_io.ctypes, "get_last_error", lambda: 32, raising=False)
    sleep = Mock()
    monkeypatch.setattr(platform_io.time, "sleep", sleep)
    budget = AnalysisBudget(AnalysisLimits())

    def write():
        if kind == "bytes":
            _atomic_write_bytes(path, b"new contents", budget=budget)
        elif kind == "npy":
            _atomic_write_npy(path, np.ones((2, 2)), budget=budget)
        else:
            _atomic_write_csv(path, ["x", "y"], np.ones((2, 2)), budget=budget)

    if persistent:
        with pytest.raises(OSError, match="MoveFileExW failed"):
            write()
        assert path.read_bytes() == b"keep original"
        assert budget.output_files == budget.output_bytes == 0
        assert len(attempts) == 3
    else:
        write()
        assert path.read_bytes() != b"keep original"
        assert budget.output_files == 1
        assert budget.output_bytes == path.stat().st_size
        assert len(attempts) == 2
    assert sleep.call_count == len(attempts) - 1
    assert len(set(attempts)) == 1
    assert list(tmp_path.iterdir()) == [path]


def test_runtime_fft_rejected_before_call_and_previous_export_retained(tmp_path, analysis_input):
    capture, recipe = analysis_input
    recipe.write_text('schema="wavebench.analysis_recipe.v1"\noperations=[{op="export",name="before",formats=["npy"]},{op="fft"},{op="export",name="after",formats=["npy"]}]\n[resources]\nmax_fft_length=64\n')
    with patch("wavebench.services.run_pipeline.fft_signal", side_effect=AssertionError("too late")) as fft:
        result = run_analysis(capture, 1, recipe, tmp_path / "analysis")
    fft.assert_not_called()
    assert result["artifact"]["analysis_pipeline"]["error"]["code"] == "resource_limit_exceeded"
    assert (tmp_path / "analysis/exports/before.npy").is_file()
    with pytest.raises(AnalysisResourceError):
        check_analysis(capture, 1, recipe)


def test_custom_profile_intent_binding_and_default_shape(tmp_path):
    plan = plan_for(tmp_path, [EXPORT])
    config = make_config(str(tmp_path))
    default = build_execution_intent(plan, config)
    assert default.schema == "wavebench.execution_intent.v1"
    assert "analysis_resources" not in default.as_dict()
    profile = replace(AnalysisLimits(), max_fft_length=64)
    custom = build_execution_intent(plan, config, resource_limits=profile)
    assert custom.schema == "wavebench.execution_intent.v2"
    assert custom.intent_digest != default.intent_digest
    verify_execution_intent(custom.as_dict(), plan, config, resource_limits=profile)
    with pytest.raises(ExecutionIntentError):
        verify_execution_intent(custom.as_dict(), plan, config)
    altered = custom.as_dict()
    altered["analysis_resources"] = AnalysisLimits().evidence()
    with pytest.raises(ExecutionIntentError):
        verify_execution_intent(altered, plan, config, resource_limits=profile)


def test_static_resource_rejection_precedes_hardware(tmp_path):
    from wavebench.services.run_service import RunService
    from wavebench.logging import CommandLogger

    plan = plan_for(tmp_path, [dict(op="psd", **(PARAMS | dict(nfft=2**21))), EXPORT])
    service = RunService(config=make_config(str(tmp_path)), logger=CommandLogger())
    with patch.object(service, "_run_instrument_lifecycle") as hardware:
        with pytest.raises(AnalysisResourceError, match="max_fft_length"):
            service.run(plan)
    hardware.assert_not_called()


def test_metadata_counts_towards_output_limit(tmp_path, analysis_input):
    capture, recipe = analysis_input
    result = run_analysis(capture, 1, recipe, tmp_path / "analysis",
                          resource_limits=replace(AnalysisLimits(), max_output_bytes=100))
    assert result["status"] == "failed"
    assert result["error"]["code"] == "resource_limit_exceeded"


def test_recipe_memory_admission_before_parse(tmp_path, analysis_input):
    from wavebench.services.analysis_service import load_analysis_recipe

    _, recipe = analysis_input
    with patch("wavebench.services.analysis_service.tomllib.loads", side_effect=AssertionError("parse too soon")):
        with pytest.raises(AnalysisResourceError, match="max_working_bytes"):
            load_analysis_recipe(recipe, replace(AnalysisLimits(), max_working_bytes=1))


def test_pipeline_and_offline_metadata_file_counts():
    operations = [{"op": "measure", "metrics": ["voltage_rms_v"]}]
    limits = replace(AnalysisLimits(), max_output_files=2)
    check_static(operations, limits)
    with pytest.raises(AnalysisResourceError, match="max_output_files"):
        check_static(operations, limits, metadata_files=3)


@pytest.mark.parametrize("format", ["npy", "csv"])
def test_report_streaming_extrema_and_fingerprint(tmp_path, format):
    count = 10001
    data = np.column_stack((np.arange(count), np.zeros(count)))
    data[5000, 1] = 10
    entries = list(_export_signal(run_dir=tmp_path, processing_dir=tmp_path,
                                 signal=TimeSignal(data[:, 0], data[:, 1]), name="test", formats=[format]))
    item = entries[0]
    with patch("numpy.loadtxt", side_effect=AssertionError("no full CSV read")), \
         patch("numpy.load", side_effect=AssertionError("no unguarded NPY read")):
        sampled, digest = read_curve(tmp_path / item["path"], item, 1, AnalysisLimits())
    assert len(sampled) <= 1200
    assert sampled[:, 1].max() == 10
    assert digest == sha256(np.asarray(data, dtype="<f8").tobytes()).hexdigest()


def test_report_curve_limit_is_warning(tmp_path, analysis_input):
    capture, recipe = analysis_input
    outputs = [tmp_path / "one", tmp_path / "two"]
    for output in outputs:
        run_analysis(capture, 1, recipe, output)
    html = write_analysis_report(outputs, tmp_path / "report.html",
                                 resource_limits=replace(AnalysisLimits(), max_report_curves=1)).read_text()
    assert html.count("<polyline") == 1
    assert "max_report_curves" in html


@pytest.mark.parametrize("file_format", ["npy", "csv"])
def test_file_and_path_stat_can_have_different_ids(tmp_path, monkeypatch, file_format):
    import os
    from wavebench.report.analysis import read_curve
    # Python's Windows stat/fstat may report different identities for one file.
    original = os.fstat
    class DescriptorStat:
        def __init__(self, value):
            self.value = value
        def __getattr__(self, name):
            if name in {"st_dev", "st_ino"}:
                return 0
            return getattr(self.value, name)
    monkeypatch.setattr(os, "fstat", lambda fd: DescriptorStat(original(fd)))
    values = np.column_stack((np.arange(16.), np.ones(16)))
    path = tmp_path / f"signal.{file_format}"
    if file_format == "npy":
        np.save(path, values)
        loaded, _ = load_waveform(path, AnalysisLimits())
        np.testing.assert_array_equal(loaded.as_array(), values)
    else:
        _atomic_write_csv(path, ["time_s", "voltage_v"], values)
    item = {"format": file_format, "path": path.name, "sha256": sha256(path.read_bytes()).hexdigest(),
            "domain": "time", "columns": ["time_s", "voltage_v"]}
    read_curve(path, item, 1, AnalysisLimits())
