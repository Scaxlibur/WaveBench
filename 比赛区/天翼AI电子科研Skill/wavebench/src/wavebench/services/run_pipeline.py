from __future__ import annotations

import csv
from hashlib import sha256
from importlib import import_module
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Iterator

import numpy as np
from wavebench.data.analysis_resources import AnalysisBudget, AnalysisLimits, AnalysisResourceError, check_static
from wavebench.data.analysis_control import checkpoint, cancel_signal
from wavebench.data.analysis_io import load_waveform, BLOCK_ROWS, read_json_bounded

from wavebench.data.signal_pipeline import (
    FILTER_BLOCK_SAMPLES,
    FrequencySignal,
    FirFilterResult,
    IirFilterResult,
    PsdSignal,
    TimeSignal,
    detrend_linear,
    fft_signal,
    filter_fir,
    filter_iir,
    measure_frequency,
    measure_time,
    remove_dc,
    validate_waveform,
    window_signal,
    welch_psd,
)
from wavebench.errors import ConfigError, DataError, error_envelope
from wavebench.data.pipeline_operations import measure_band, detect_peaks, smooth_signal, resample_signal
from wavebench.services.run_analysis import evaluate_expect
from wavebench.services.run_artifacts import RunStepRecord
from wavebench.services.run_plan import RunPlan, RunStep
from wavebench.services.platform_io import _replace_file


ANALYSIS_PIPELINE_SCHEMA = "wavebench.analysis_pipeline.v1"
ANALYSIS_METRICS_SCHEMA = "wavebench.analysis_metrics.v1"


def ensure_analysis_pipeline_dependencies(plan: RunPlan) -> None:
    ensure_operation_dependencies([
        operation
        for step in plan.steps if step.kind == "analysis.pipeline"
        for operation in step.fields["operations"]
    ])


def ensure_operation_dependencies(all_operations: list[dict[str, Any]]) -> None:
    operations = [
        operation
        for operation in all_operations
        if operation["op"] in {"filter", "psd", "peaks", "resample", "spectral_quality"}
        or operation["op"] == "smooth" and operation["method"] == "savgol"
    ]
    if not operations:
        return
    required_functions: set[str] = set()
    for operation in operations:
        if operation["op"] == "resample":
            required_functions.update({"resample_poly", "firwin"})
        elif operation["op"] == "smooth":
            required_functions.add("savgol_coeffs")
        elif operation["op"] in {"peaks", "spectral_quality"}:
            required_functions.add("find_peaks")
        elif operation["op"] == "psd":
            required_functions.update({"welch", "get_window"})
        elif operation["family"] == "fir":
            required_functions.add("firwin")
            required_functions.add(
                "lfilter" if operation["mode"] == "causal" else "filtfilt"
            )
        else:
            required_functions.update({
                {
                    "butterworth": "butter",
                    "chebyshev1": "cheby1",
                    "chebyshev2": "cheby2",
                    "elliptic": "ellip",
                }[operation["design"]],
                "sos2zpk",
                "sosfilt" if operation["mode"] == "causal" else "sosfiltfilt",
            })
    try:
        scipy_signal = import_module("scipy.signal")
    except ImportError as exc:
        raise ConfigError(
            "analysis processing requires SciPy; install WaveBench with `.[analysis]`"
        ) from exc
    missing = sorted(
        name
        for name in required_functions
        if not callable(getattr(scipy_signal, name, None))
    )
    if missing:
        raise ConfigError(
            "analysis processing requires compatible SciPy signal support "
            f"({', '.join(missing)}); install WaveBench with `.[analysis]`"
        )


def execute_analysis_pipeline(
    *,
    run_dir: Path,
    step: RunStep,
    source_step: RunStep,
    source_record: RunStepRecord | None,
    resource_limits: AnalysisLimits | None = None,
    execution_policy=None, cancel_event=None,
) -> dict[str, Any]:
    limits = (resource_limits or AnalysisLimits()).tighten(step.fields.get("resources"))
    check_static(step.fields["operations"], limits)
    processing_dir = run_dir / "processing" / (
        f"{step.index:02d}_{step.id or 'analysis_pipeline'}"
    )
    if execution_policy is not None:
        from .analysis_execution import supervise
        return supervise(execute_analysis_pipeline,
            dict(run_dir=run_dir, step=step, source_step=source_step, source_record=source_record,
                 resource_limits=limits), policy=execution_policy, run_dir=run_dir,
            processing_dir=processing_dir, fields=step.fields, limits=limits, cancel_event=cancel_event,
            source={'step': source_step.id, 'step_index': source_step.index,
                    'status': source_record.status if source_record else 'unavailable'})

    def load_source() -> tuple[dict[str, Any], TimeSignal]:
        _, details, waveform = _load_source_waveform(
            run_dir=run_dir, source_step=source_step, source_record=source_record, resource_limits=limits,
        )
        return details, waveform

    return execute_pipeline(
        run_dir=run_dir, processing_dir=processing_dir, fields=step.fields,
        source={
            "step": source_step.id, "step_index": source_step.index,
            "status": source_record.status if source_record is not None else "unavailable",
        },
        load_source=load_source,
        resource_limits=limits,
    )


def execute_pipeline(
    *, run_dir: Path, processing_dir: Path, fields: dict[str, Any],
    source: dict[str, Any], load_source: Callable[[], tuple[dict[str, Any], np.ndarray]],
    schema: str = ANALYSIS_PIPELINE_SCHEMA,
    resource_limits: AnalysisLimits | None = None,
) -> dict[str, Any]:
    limits = (resource_limits or AnalysisLimits()).tighten(fields.get("resources"))
    check_static(fields["operations"], limits, metadata_files=3 if schema == "wavebench.offline_pipeline.v1" else 2)
    budget = AnalysisBudget(limits)
    processing_dir.mkdir(parents=True, exist_ok=False)
    metrics_path = processing_dir / "metrics.json"
    manifest_path = processing_dir / "manifest.json"

    operations = fields["operations"]
    metrics: dict[str, float | None] = {
        metric: None
        for operation in operations
        if operation["op"] == "measure"
        for metric in operation["metrics"]
    }
    for operation in operations:
        if operation["op"] in {"measure_band", "peaks", "spectral_quality"}:
            metrics.update({f"{operation['name']}_{metric}": None for metric in operation["metrics"]})
    warnings: list[str] = []
    exports: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []
    peaks: list[dict[str, Any]] = []
    transformations: list[dict[str, Any]] = []
    sampling: dict[str, Any] | None = None
    window: dict[str, Any] | None = None
    psd: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    failed_stage: str | None = None

    def save_progress():
        if cancel_signal.get() is None:
            return
        document = dict(schema=schema, status='running', partial=True, source=source,
            operations=operations, stages=stages, sampling=sampling, window=window,
            warnings=warnings, exports=exports, peaks=peaks, filters=filters, psd=psd,
            transformations=transformations,
            metrics=_derived_relative(metrics_path, run_dir),
            resources={**limits.evidence(), 'work_units': budget.work_units,
                       'data_output_bytes': budget.output_bytes, 'data_output_files': budget.output_files})
        _atomic_write_json(metrics_path, {'schema': ANALYSIS_METRICS_SCHEMA, 'metrics': metrics})
        _atomic_write_json(manifest_path, document)

    try:
        save_progress()
        checkpoint()
        source_details, waveform = load_source()
        source.update(source_details)
        if isinstance(waveform, TimeSignal):
            signal = waveform
        else:
            budget.source(len(waveform), np.asarray(waveform).dtype.itemsize)
            signal = validate_waveform(waveform)
        del waveform
        sampling = _time_sampling(signal)
        stages.append({"stage": "source", "status": "ok", "domain": "time"})

        for operation_index, operation in enumerate(operations):
            op = operation["op"]
            stage = {
                "index": operation_index,
                "op": op,
                "status": "running",
                "input_domain": _domain(signal),
            }
            stages.append(stage)
            try:
                save_progress()
                checkpoint()
                count = len(signal.time_s) if isinstance(signal, TimeSignal) else len(signal.frequency_hz)
                retained = sum(item.get("retained_count", 0) * 1024 for item in peaks)
                stage["resources"] = budget.stage(operation, count, domain=_domain(signal), retained_bytes=retained)
                if op == "remove_dc":
                    assert isinstance(signal, TimeSignal)
                    signal = remove_dc(signal)
                elif op == "detrend":
                    assert isinstance(signal, TimeSignal)
                    signal = detrend_linear(signal)
                elif op in {"smooth", "resample"}:
                    assert isinstance(signal, TimeSignal)
                    signal, metadata = (smooth_signal(signal, operation) if op == "smooth"
                                        else resample_signal(signal, operation))
                    metadata["operation_index"] = operation_index
                    stage["transformation"] = metadata
                    transformations.append(metadata)
                    if op == "resample":
                        sampling = _time_sampling(signal)
                        sampling.update({"sample_interval_s": metadata["sample_interval_s"],
                                         "sample_rate_hz": metadata["sample_rate_hz"]})
                elif op == "filter":
                    assert isinstance(signal, TimeSignal)
                    if operation["family"] == "fir":
                        result = filter_fir(
                            signal,
                            response=operation["response"],
                            cutoff_hz=operation["cutoff_hz"],
                            numtaps=operation["numtaps"],
                            mode=operation["mode"],
                        )
                        filter_metadata = _fir_filter_metadata(
                            operation_index, operation, result
                        )
                    else:
                        result = filter_iir(
                            signal,
                            design=operation["design"],
                            response=operation["response"],
                            cutoff_hz=operation["cutoff_hz"],
                            order=operation["order"],
                            mode=operation["mode"],
                            ripple_db=operation.get("ripple_db"),
                            attenuation_db=operation.get("attenuation_db"),
                        )
                        filter_metadata = _iir_filter_metadata(
                            operation_index, operation, result
                        )
                    signal = result.signal
                    filters.append(filter_metadata)
                    stage["filter"] = filter_metadata
                    sampling.update({
                        "sample_interval_s": result.sample_interval_s,
                        "sample_rate_hz": result.sample_rate_hz,
                    })
                    del result
                elif op == "window":
                    assert isinstance(signal, TimeSignal)
                    signal = window_signal(signal, operation["name"])
                    window = {
                        "name": signal.window_name,
                        "coherent_gain": signal.coherent_gain,
                    }
                elif op == "fft":
                    assert isinstance(signal, TimeSignal)
                    signal = fft_signal(signal)
                    sampling.update({
                        "sample_interval_s": signal.sample_interval_s,
                        "sample_rate_hz": signal.sample_rate_hz,
                        "resolution_hz": signal.resolution_hz,
                    })
                elif op == "psd":
                    assert isinstance(signal, TimeSignal)
                    signal = welch_psd(
                        signal, **{key: value for key, value in operation.items() if key != "op"}
                    )
                    rate = 1.0 / signal.sample_interval_s
                    psd = {
                        **signal.parameters,
                        "operation_index": operation_index,
                        "execution_function": "scipy.signal.welch",
                        "algorithm": ("welch_segment_mean.v1" if operation["average"] == "mean"
                                      else "scipy_welch_median.v1"),
                        "scipy_version": signal.scipy_version,
                        "sample_rate_hz": rate,
                        "window_periodic": True,
                        "window_power_gain": signal.window_power_gain,
                        "window_sha256": signal.window_sha256,
                        "segment_count": signal.segment_count,
                        "discarded_tail_samples": signal.discarded_tail_samples,
                        "bin_spacing_hz": rate / operation["nfft"],
                        "segment_frequency_scale_hz": rate / operation["nperseg"],
                        "scaling": "density",
                        "units": "V^2/Hz",
                        "return_onesided": True,
                        "normalization": "sample_rate_hz * sum(window ** 2)",
                    }
                    stage["psd"] = psd
                    sampling.update({
                        "sample_interval_s": signal.sample_interval_s,
                        "sample_rate_hz": rate,
                    })
                    psd_warnings = []
                    if signal.segment_count == 1:
                        psd_warnings.append("PSD has only one segment; no segment averaging")
                    if signal.discarded_tail_samples:
                        psd_warnings.append(
                            f"PSD discarded {signal.discarded_tail_samples} trailing samples"
                        )
                    if psd_warnings:
                        stage["warnings"] = psd_warnings
                        _extend_unique(warnings, psd_warnings)
                elif op == "peaks":
                    detected = detect_peaks(signal, operation)
                    peak_dir = processing_dir / "peaks"
                    peak_dir.mkdir(exist_ok=True)
                    json_path = peak_dir / f"{operation['name']}.json"
                    csv_path = peak_dir / f"{operation['name']}.csv"
                    _atomic_write_json(json_path, detected, budget=budget)
                    columns = ["index", "position", "value", "prominence", "width", "polarity"]
                    _atomic_write_csv(csv_path, columns, np.asarray([
                        [row[key] for key in columns] for row in detected["peaks"]
                    ]).reshape(-1, len(columns)), budget=budget)
                    metadata = {key: value for key, value in detected.items() if key != "peaks"}
                    metadata.update({
                        "json": _derived_relative(json_path, run_dir),
                        "csv": _derived_relative(csv_path, run_dir),
                        "json_sha256": _sha256_file(json_path), "csv_sha256": _sha256_file(csv_path),
                    })
                    peaks.append(metadata)
                    stage["peaks"] = metadata
                    metrics[f"{operation['name']}_count"] = detected["count"]
                    if detected["truncated"]:
                        message = f"{operation['name']}: peak table truncated to {detected['retained_count']} rows"
                        stage["warnings"] = [message]
                        _extend_unique(warnings, [message])
                elif op == "spectral_quality":
                    from wavebench.data.spectral_quality import spectral_quality
                    measured, metadata, operation_warnings = spectral_quality(signal, operation)
                    metrics.update(measured)
                    stage["measurement"] = metadata
                    stage["warnings"] = operation_warnings
                    _extend_unique(warnings, operation_warnings)
                elif op == "measure_band":
                    assert isinstance(signal, PsdSignal)
                    measured, metadata, operation_warnings = measure_band(signal, operation)
                    metrics.update(measured)
                    stage["measurement"] = metadata
                    if operation_warnings:
                        stage["warnings"] = operation_warnings
                        _extend_unique(warnings, operation_warnings)
                elif op == "measure":
                    if isinstance(signal, TimeSignal):
                        measured = measure_time(signal, operation["metrics"])
                        operation_warnings: list[str] = []
                    elif isinstance(signal, FrequencySignal):
                        measured, operation_warnings = measure_frequency(
                            signal, operation["metrics"]
                        )
                    else:
                        raise DataError("PSD does not support scalar metrics")
                    metrics.update(measured)
                    _extend_unique(warnings, operation_warnings)
                    if operation_warnings:
                        stage["warnings"] = operation_warnings
                elif op == "export":
                    exported: list[dict[str, Any]] = []
                    for item in _export_signal(
                        run_dir=run_dir,
                        processing_dir=processing_dir,
                        signal=signal,
                        name=operation["name"],
                        formats=operation["formats"],
                        budget=budget,
                    ):
                        exported.append(item)
                        exports.append(item)
                        save_progress()
                    stage["exports"] = [item["path"] for item in exported]
                else:  # pragma: no cover - RunPlan validation owns this invariant
                    raise DataError(f"unsupported analysis operation: {op}")
            except Exception:
                stage["status"] = "failed"
                raise
            stage["status"] = "ok"
            stage["output_domain"] = _domain(signal)
            save_progress()
    except Exception as exc:  # noqa: BLE001 - analysis failures are step artifacts
        failed_stage = _failed_stage(stages)
        failure = error_envelope(
            exc,
            operation=(
                f"analysis.pipeline.{failed_stage}"
                if failed_stage is not None
                else "analysis.pipeline"
            ),
        )
        if not stages or stages[0].get("stage") != "source":
            stages.insert(0, {"stage": "source", "status": "failed"})
        for operation_index in range(len(stages) - 1, len(operations)):
            operation = operations[operation_index]
            stages.append({
                "index": operation_index,
                "op": operation["op"],
                "status": "skipped",
            })

    status = "failed" if failure is not None else "ok"
    partial = failure is not None and (
        bool(exports)
        or any(stage.get("status") == "ok" and "index" in stage for stage in stages)
    )
    metrics_document = {
        "schema": ANALYSIS_METRICS_SCHEMA,
        "metrics": metrics,
    }
    manifest: dict[str, Any] = {
        "schema": schema,
        "status": status,
        "partial": partial,
        "source": source,
        "operations": operations,
        "stages": stages,
        "sampling": sampling,
        "window": window,
        "metrics": _derived_relative(metrics_path, run_dir),
        "warnings": warnings,
        "exports": exports,
        "definitions": {
            "amplitude": "single_sided_peak_v",
            "noise_floor_v": "median_non_dc_non_peak_bin_peak_amplitude_v",
            "thd_ratio": "rss_in_band_harmonic_2_through_5_over_fundamental_peak",
        },
    }
    if filters:
        manifest["filters"] = filters
    if psd is not None:
        manifest["psd"] = psd
    if peaks:
        manifest["peaks"] = peaks
    if transformations:
        manifest["transformations"] = transformations
    if failed_stage is not None:
        manifest["failed_stage"] = failed_stage
    if failure is not None:
        manifest["error"] = failure

    manifest["resources"] = {**limits.evidence(), "work_units": budget.work_units,
                             "data_output_bytes": budget.output_bytes,
                             "data_output_files": budget.output_files}

    try:
        _atomic_write_json(metrics_path, metrics_document, budget=budget)
        _atomic_write_json(manifest_path, manifest, budget=budget)
    except AnalysisResourceError as exc:
        # Diagnostic metadata is permitted after exhaustion, never a successful oversized result.
        status, failed_stage = "failed", "metadata"
        failure = error_envelope(exc, operation="analysis.pipeline.metadata")
        manifest.update(status=status, failed_stage=failed_stage, error=failure, partial=bool(exports or peaks))
        _atomic_write_json(metrics_path, metrics_document)
        _atomic_write_json(manifest_path, manifest)

    pipeline_artifact: dict[str, Any] = {
        "schema": schema,
        "status": status,
        "manifest": _derived_relative(manifest_path, run_dir),
        "metrics": _derived_relative(metrics_path, run_dir),
        "source_step": source.get("step"),
        "resources": manifest["resources"],
        "source_status": source["status"],
        "operations": operations,
        "warnings": warnings,
        "exports": exports,
    }
    if failed_stage is not None:
        pipeline_artifact["failed_stage"] = failed_stage
    if peaks:
        pipeline_artifact["peaks"] = peaks
    if failure is not None:
        pipeline_artifact["error"] = failure

    artifact: dict[str, Any] = {
        "analysis_pipeline": pipeline_artifact,
        "metrics": metrics,
    }
    if "expect" in fields:
        artifact["expect"] = evaluate_expect(metrics, fields["expect"])
    return artifact


def _fir_filter_metadata(
    operation_index: int,
    operation: dict[str, Any],
    result: FirFilterResult,
) -> dict[str, Any]:
    numtaps = operation["numtaps"]
    mode = operation["mode"]
    metadata: dict[str, Any] = {
        "operation_index": operation_index,
        "family": "fir",
        "response": operation["response"],
        "cutoff_hz": operation["cutoff_hz"],
        "numtaps": numtaps,
        "sample_rate_hz": result.sample_rate_hz,
        "design_function": "scipy.signal.firwin",
        "design_window": "hamming",
        "scale": True,
        "scipy_version": result.scipy_version,
        "coefficients_sha256": sha256(
            np.asarray(result.taps, dtype="<f8").tobytes()
        ).hexdigest(),
        "mode": mode,
        "execution_function": (
            "scipy.signal.lfilter" if mode == "causal" else "scipy.signal.filtfilt"
        ),
        "passes": 1 if mode == "causal" else 2,
        "effective_magnitude_response": (
            "single_pass" if mode == "causal" else "single_pass_squared"
        ),
        "nominal_single_pass_group_delay_samples": (numtaps - 1) / 2,
        "nominal_single_pass_group_delay_s": (
            (numtaps - 1) / 2 * result.sample_interval_s
        ),
    }
    if mode == "causal":
        metadata.update({
            "boundary": "zero_initial_state",
            "initial_state": "zeros",
            "algorithm": "causal_blocks.v1",
            "block_samples": FILTER_BLOCK_SAMPLES,
        })
    else:
        metadata.update({
            "boundary": "odd_extension",
            "method": "pad",
            "padtype": "odd",
            "padlen": 3 * numtaps,
        })
    return metadata


def _iir_filter_metadata(
    operation_index: int,
    operation: dict[str, Any],
    result: IirFilterResult,
) -> dict[str, Any]:
    design = operation["design"]
    order = operation["order"]
    mode = operation["mode"]
    sections = int(result.sos.shape[0])
    metadata: dict[str, Any] = {
        "operation_index": operation_index,
        "family": "iir",
        "design": design,
        "response": operation["response"],
        "cutoff_hz": operation["cutoff_hz"],
        "order": order,
        "digital_filter_order": (
            2 * order if operation["response"] in {"bandpass", "bandstop"} else order
        ),
        "sample_rate_hz": result.sample_rate_hz,
        "design_function": {
            "butterworth": "scipy.signal.butter",
            "chebyshev1": "scipy.signal.cheby1",
            "chebyshev2": "scipy.signal.cheby2",
            "elliptic": "scipy.signal.ellip",
        }[design],
        "design_output": "sos",
        "critical_frequency_semantics": {
            "butterworth": "single_pass_minus_3_db",
            "chebyshev1": "single_pass_passband_ripple_edge",
            "chebyshev2": "single_pass_stopband_attenuation_edge",
            "elliptic": "single_pass_passband_ripple_edge",
        }[design],
        "sections": sections,
        "sos_shape": [sections, 6],
        "sos_sha256": sha256(
            np.asarray(result.sos, dtype="<f8").tobytes()
        ).hexdigest(),
        "scipy_version": result.scipy_version,
        "stable": True,
        "max_pole_magnitude": result.max_pole_magnitude,
        "mode": mode,
        "execution_function": (
            "scipy.signal.sosfilt"
            if mode == "causal"
            else "scipy.signal.sosfiltfilt"
        ),
        "passes": 1 if mode == "causal" else 2,
        "effective_magnitude_response": (
            "single_pass" if mode == "causal" else "single_pass_squared"
        ),
    }
    for name in ("ripple_db", "attenuation_db"):
        if name in operation:
            metadata[name] = operation[name]
    if mode == "causal":
        metadata.update({
            "boundary": "zero_initial_state",
            "initial_state": "zeros",
            "algorithm": "causal_blocks.v1",
            "block_samples": FILTER_BLOCK_SAMPLES,
        })
    else:
        metadata.update({
            "boundary": "odd_extension",
            "padtype": "odd",
            "padlen": result.zero_phase_padlen,
        })
    return metadata


def _load_source_waveform(
    *,
    run_dir: Path,
    source_step: RunStep,
    source_record: RunStepRecord | None,
    resource_limits: AnalysisLimits | None = None,
) -> tuple[Path, dict[str, Any], TimeSignal]:
    if source_record is None:
        raise DataError(f"source capture step {source_step.id!r} was not executed")
    package_text = source_record.artifact.get("package")
    metadata_text = source_record.artifact.get("metadata")
    if not isinstance(package_text, str) or not package_text:
        raise DataError(f"source capture step {source_step.id!r} has no package artifact")
    if not isinstance(metadata_text, str) or not metadata_text:
        raise DataError(f"source capture step {source_step.id!r} has no metadata artifact")

    package = Path(package_text).resolve()
    if not package.is_dir():
        raise DataError(f"source capture package is unavailable: {package}")
    metadata_path = _resolve_package_member(package, metadata_text, label="metadata")
    try:
        metadata = read_json_bounded(metadata_path, resource_limits or AnalysisLimits())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DataError(f"source capture metadata is unreadable: {metadata_path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise DataError("source capture metadata must be a JSON object")
    files = metadata.get("files")
    if not isinstance(files, dict):
        raise DataError("source capture metadata has no files table")
    npy_text = files.get("npy")
    if not isinstance(npy_text, str) or not npy_text:
        raise DataError("source capture metadata has no NPY artifact")
    waveform_path = _resolve_package_member(package, npy_text, label="NPY")
    waveform, source_hash = load_waveform(waveform_path, resource_limits or AnalysisLimits())

    return waveform_path, {
        "package": _run_relative(package, run_dir),
        "metadata": _run_relative(metadata_path, run_dir),
        "npy": _run_relative(waveform_path, run_dir),
        "npy_sha256": source_hash,
    }, waveform


def _resolve_package_member(package: Path, raw: str, *, label: str) -> Path:
    candidate = Path(raw)
    if ".." in candidate.parts:
        raise DataError(f"source capture {label} path must not contain '..'")
    candidates = [candidate] if candidate.is_absolute() else [Path.cwd() / candidate, package / candidate]
    package = package.resolve()
    for unresolved in candidates:
        resolved = unresolved.resolve()
        try:
            resolved.relative_to(package)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    raise DataError(f"source capture {label} path escapes its capture package or is unavailable")


def _export_signal(
    *,
    run_dir: Path,
    processing_dir: Path,
    signal: TimeSignal | FrequencySignal | PsdSignal,
    name: str,
    formats: list[str],
    budget: AnalysisBudget | None = None,
) -> Iterator[dict[str, Any]]:
    exports_dir = processing_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(signal, TimeSignal):
        columns = ["time_s", "voltage_v"]
    elif isinstance(signal, PsdSignal):
        columns = ["frequency_hz", "psd_v2_per_hz"]
    else:
        columns = ["frequency_hz", "real_v", "imaginary_v", "amplitude_v"]
    if isinstance(signal, TimeSignal):
        arrays = (signal.time_s, signal.voltage_v)
    elif isinstance(signal, PsdSignal):
        arrays = (signal.frequency_hz, signal.psd_v2_per_hz)
    else:
        arrays = (signal.frequency_hz, signal.spectrum_v.real, signal.spectrum_v.imag)
    def blocks():
        for start in range(0, len(arrays[0]), BLOCK_ROWS):
            values = [array[start:start + BLOCK_ROWS] for array in arrays]
            if isinstance(signal, FrequencySignal):
                values.append(np.abs(signal.spectrum_v[start:start + BLOCK_ROWS]))
            yield np.column_stack(values)
    for file_format in formats:
        target = exports_dir / f"{name}.{file_format}"
        if target.exists():  # pragma: no cover - parser prevents duplicate export names
            raise DataError(f"analysis export already exists: {target.name}")
        if file_format == "npy":
            _atomic_write_npy(target, None, blocks=blocks, shape=(len(arrays[0]), len(columns)), budget=budget)
        elif file_format == "csv":
            _atomic_write_csv(target, columns, None, blocks=blocks, budget=budget)
        else:  # pragma: no cover - RunPlan validation owns this invariant
            raise DataError(f"unsupported analysis export format: {file_format}")
        yield {
            "name": name,
            "format": file_format,
            "path": _derived_relative(target, run_dir),
            "sha256": _sha256_file(target),
            "columns": columns,
        }


def _atomic_write_json(path: Path, value: dict[str, Any], *, budget: AnalysisBudget | None = None) -> None:
    encoded = (
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    if budget:
        budget.limits.check("max_metadata_bytes", len(encoded), "metadata write")
    _atomic_write_bytes(path, encoded, budget=budget)


def _atomic_write_npy(path: Path, data: np.ndarray | None, *, blocks=None, shape=None,
                      budget: AnalysisBudget | None = None) -> None:
    if blocks is None:
        shape = data.shape
        def blocks():
            return (data[start:start + BLOCK_ROWS] for start in range(0, len(data), BLOCK_ROWS))
    temporary = _temporary_path(path)
    try:
        with temporary.open("wb") as file:
            np.lib.format.write_array_header_1_0(file, {"descr": np.dtype(float).str,
                "fortran_order": False, "shape": shape})
            for block in blocks():
                checkpoint()
                encoded = np.asarray(block, dtype=float, order="C").tobytes()
                if budget:
                    budget.pending_file(file.tell() + len(encoded))
                file.write(encoded)
            file.flush()
            os.fsync(file.fileno())
        _replace_file(temporary, path)
        if budget:
            budget.committed_file(path.stat().st_size)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_csv(path: Path, columns: list[str], data: np.ndarray | None, *, blocks=None,
                      budget: AnalysisBudget | None = None) -> None:
    if blocks is None:
        def blocks():
            return (data[start:start + BLOCK_ROWS] for start in range(0, len(data), BLOCK_ROWS))
    temporary = _temporary_path(path)
    try:
        with temporary.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(columns)
            for block in blocks():
                checkpoint()
                # Only this bounded block becomes Python objects; numeric formatting stays unchanged.
                import io
                buffer = io.StringIO(newline="")
                csv.writer(buffer).writerows(block.tolist())
                encoded = buffer.getvalue()
                if budget:
                    budget.pending_file(file.tell() + len(encoded.encode("utf-8")))
                file.write(encoded)
            if budget:
                budget.pending_file(file.tell())
            file.flush()
            os.fsync(file.fileno())
        _replace_file(temporary, path)
        if budget:
            budget.committed_file(path.stat().st_size)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_bytes(path: Path, data: bytes, *, budget: AnalysisBudget | None = None) -> None:
    if budget:
        budget.pending_file(len(data))
    temporary = _temporary_path(path)
    try:
        with temporary.open("xb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        _replace_file(temporary, path)
        if budget:
            budget.committed_file(len(data))
    finally:
        temporary.unlink(missing_ok=True)


def _temporary_path(path: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(raw_path)
    temporary.unlink()
    return temporary


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            checkpoint()
            digest.update(chunk)
    return digest.hexdigest()


def _time_sampling(signal: TimeSignal) -> dict[str, Any]:
    return {
        "samples": int(signal.time_s.size),
        "time_start_s": float(signal.time_s[0]),
        "time_stop_s": float(signal.time_s[-1]),
        "strictly_increasing": True,
    }


def _domain(signal: TimeSignal | FrequencySignal | PsdSignal) -> str:
    if isinstance(signal, PsdSignal):
        return "psd"
    return "time" if isinstance(signal, TimeSignal) else "frequency"


def _failed_stage(stages: list[dict[str, Any]]) -> str | None:
    for stage in reversed(stages):
        if stage.get("status") == "failed":
            if "index" in stage:
                return f"operations[{stage['index']}]"
            return str(stage.get("stage", "source"))
    return "source"


def _run_relative(path: Path, run_dir: Path) -> str:
    return Path(os.path.relpath(path.resolve(), run_dir.resolve())).as_posix()


def _derived_relative(path: Path, run_dir: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError as exc:  # pragma: no cover - paths are constructed below run_dir
        raise DataError("analysis derived path escapes the run directory") from exc


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)
