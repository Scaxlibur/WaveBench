"""Offline recipes reuse the RunPlan operator contract and execution engine."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import tomllib
from typing import Any


from wavebench import __version__
from wavebench.data.packages import CapturePackage, _capture_channels
from wavebench.data.analysis_resources import AnalysisLimits, AnalysisBudget, AnalysisResourceError, check_static
from wavebench.data.analysis_io import load_waveform, read_json_bounded
from wavebench.errors import ConfigError, DataError, error_envelope
from wavebench.services.run_pipeline import (
    _atomic_write_json, _resolve_package_member,
    ensure_operation_dependencies, execute_pipeline,
)
from wavebench.services.run_plan import normalize_analysis_operations


RECIPE_SCHEMA = "wavebench.analysis_recipe.v1"
RESULT_SCHEMA = "wavebench.analysis.v1"


def load_analysis_recipe(path: str | Path, resource_limits: AnalysisLimits | None = None) -> dict[str, Any]:
    environment = resource_limits or AnalysisLimits()
    try:
        environment.check("max_metadata_bytes", Path(path).stat().st_size, "recipe")
        environment.check("max_working_bytes", Path(path).stat().st_size * 32 + 65536, "recipe parsing")
        fields = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read analysis recipe: {exc}") from exc
    if fields.get("schema") != RECIPE_SCHEMA:
        raise ConfigError(f"analysis recipe schema must be {RECIPE_SCHEMA}")
    if set(fields) - {"schema", "operations", "expect", "resources"}:
        raise ConfigError("analysis recipe has unknown fields")
    if "operations" not in fields:
        raise ConfigError("analysis recipe requires operations")
    normalize_analysis_operations("recipe", fields)
    check_static(fields["operations"], environment.tighten(fields.get("resources")), metadata_files=3)
    ensure_operation_dependencies(fields["operations"])
    return fields


def load_analysis_source(capture: Path, channel: int, resource_limits: AnalysisLimits | None = None):
    limits = resource_limits or AnalysisLimits()
    if isinstance(channel, bool) or not isinstance(channel, int) or channel < 1:
        raise ConfigError("analysis channel must be a positive integer")
    package_path = capture.resolve()
    _resolve_package_member(package_path, "metadata.json", label="metadata")
    try:
        metadata = read_json_bounded(package_path / "metadata.json", limits)
        if not isinstance(metadata, dict):
            raise ValueError("capture metadata must be an object")
        package = CapturePackage(package_path, package_path / "metadata.json", metadata, _capture_channels(metadata))
    except (TypeError, ValueError, KeyError) as exc:
        raise DataError(f"invalid capture metadata: {exc}") from exc
    candidates = [item for item in package.channels if item.channel == channel]
    if len(candidates) != 1:
        raise DataError(f"capture must contain exactly one channel {channel}")
    raw = candidates[0].files.get("npy")
    if not isinstance(raw, str) or not raw:
        raise DataError("selected capture channel has no NPY")
    path = _resolve_package_member(package_path, raw, label="NPY")
    try:
        waveform, source_hash = load_waveform(path, limits)
    except (OSError, ValueError) as exc:
        raise DataError(f"cannot load capture waveform: {exc}") from exc
    return {
        "kind": "capture_package", "package": str(package_path), "channel": channel,
        "npy": path.relative_to(package_path).as_posix(), "npy_sha256": source_hash,
        "status": package.metadata.get("status") if isinstance(package.metadata.get("status"), str) else None,
    }, waveform


def check_analysis(capture: Path, channel: int, recipe: Path, *, resource_limits: AnalysisLimits | None = None, execution_policy=None) -> dict[str, Any]:
    if execution_policy is not None:
        execution_policy.preflight()
    fields = load_analysis_recipe(recipe, resource_limits)
    limits = (resource_limits or AnalysisLimits()).tighten(fields.get("resources"))
    source, data = load_analysis_source(capture, channel, limits)
    budget = AnalysisBudget(limits)
    count, domain = len(data.time_s), "time"
    for operation in fields["operations"]:
        budget.stage(operation, count, domain=domain)
        if operation["op"] == "resample":
            count = (count * operation["up"] + operation["down"] - 1) // operation["down"]
        elif operation["op"] in {"fft", "psd"}:
            count = (count if operation["op"] == "fft" else operation["nfft"]) // 2 + 1
            domain = "frequency" if operation["op"] == "fft" else "psd"
        elif operation["op"] == "export":
            cols = 4 if domain == "frequency" else 2
            budget.output_bytes += sum(count * cols * (8 if fmt == "npy" else 32) + 1024 for fmt in operation["formats"])
    return {"schema": "wavebench.analysis_check.v1", "status": "ok", "source": source,
            "samples": len(data.time_s), "recipe": fields, "resources": limits.evidence(),
            **({"execution": execution_policy.evidence()} if execution_policy is not None else {})}


def run_analysis(capture: Path, channel: int, recipe: Path, output: Path, *, resource_limits: AnalysisLimits | None = None, execution_policy=None, cancel_event=None, _output_limits=None) -> dict[str, Any]:
    if execution_policy is not None:
        execution_policy.preflight()
    fields = load_analysis_recipe(recipe, resource_limits)
    limits = (resource_limits or AnalysisLimits()).tighten(fields.get("resources"))
    if _output_limits is not None:
        from dataclasses import replace
        limits = replace(limits, **{key: min(getattr(limits, key), getattr(_output_limits, key))
                         for key in ('max_output_bytes', 'max_output_files', 'max_temp_bytes')})
    capture = capture.resolve()
    output = output.resolve()
    if output.exists():
        raise ConfigError("analysis output must be a new directory")
    if output == capture or capture in output.parents or output in capture.parents:
        raise ConfigError("analysis output must be separate from the capture package")
    if any((parent / "run.json").exists() for parent in output.parents):
        raise ConfigError("analysis output must not modify an existing run")
    source = {"kind": "capture_package", "package": str(capture), "channel": channel, "status": None}
    options = dict(output=output, capture=capture, channel=channel, fields=fields, source=source, limits=limits)
    if execution_policy is not None:
        from .analysis_execution import supervise
        artifact = supervise(_execute_offline, options, policy=execution_policy, run_dir=output,
            processing_dir=output, fields=fields, source=source, limits=limits,
            schema="wavebench.offline_pipeline.v1", cancel_event=cancel_event)
    else:
        artifact = _execute_offline(**options)
    failed = artifact["analysis_pipeline"]["status"] == "failed"
    if "expect" in artifact:
        failed = failed or artifact["expect"]["status"] != "ok"
    result = {
        "schema": RESULT_SCHEMA, "status": "failed" if failed else "ok",
        "wavebench_version": __version__, "source": source, "recipe": fields,
        "recipe_sha256": sha256(json.dumps(fields, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "artifact": artifact,
    }
    budget = AnalysisBudget(limits)
    pipeline = artifact["analysis_pipeline"]
    evidence = pipeline["resources"]
    budget.output_bytes = evidence["data_output_bytes"] + sum(
        (output / name).stat().st_size for name in (pipeline["manifest"], pipeline["metrics"])
    )
    budget.output_files = evidence["data_output_files"] + 2
    try:
        _atomic_write_json(output / "analysis.json", result, budget=budget)
    except AnalysisResourceError as exc:
        result["status"] = "failed"
        result["error"] = error_envelope(exc, operation="analysis.result_metadata")
        _atomic_write_json(output / "analysis.json", result)
    return result


def _execute_offline(*, output, capture, channel, fields, source, limits):
    execution_fields = dict(fields)
    if "resources" in fields:
        execution_fields["resources"] = {key: min(value, getattr(limits, key)) for key, value in fields["resources"].items()}
    return execute_pipeline(
        run_dir=output, processing_dir=output, fields=execution_fields, source=source,
        load_source=lambda: load_analysis_source(capture, channel, limits), resource_limits=limits,
        schema="wavebench.offline_pipeline.v1",
    )
