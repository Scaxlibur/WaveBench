"""Finite analysis budgets. Estimates are admission checks, not OS memory limits."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import tomllib
from typing import Any

from wavebench.errors import ConfigError, DataError


class AnalysisResourceError(DataError):
    code = "resource_limit_exceeded"

    def __init__(self, dimension: str, limit: int, requested: int, stage: str):
        super().__init__(f"analysis {stage}: {dimension} requires {requested}, limit is {limit}; "
                         "reduce the workload or explicitly select a larger resource profile")
        self.evidence = dict(dimension=dimension, limit=limit, requested=requested, stage=stage)

    def to_envelope(self, *, operation=None, details=None, cause=None):
        return super().to_envelope(operation=operation, details={**(details or {}), **self.evidence}, cause=cause)


@dataclass(frozen=True)
class AnalysisLimits:
    max_working_bytes: int = 512 * 1024**2
    max_input_samples: int = 20_000_000
    max_fft_length: int = 1_048_576
    max_fir_taps: int = 4095
    max_zero_phase_fir_taps: int = 255
    max_work_units: int = 2_000_000_000
    max_output_bytes: int = 1024**3
    max_temp_bytes: int = 1024**3
    max_peak_candidates: int = 100_000
    max_report_curves: int = 32
    max_operations: int = 128
    max_output_files: int = 256
    max_metadata_bytes: int = 8 * 1024**2

    def __post_init__(self):
        for key, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
                raise ConfigError(f"analysis resource {key} must be an integer in [1, 2^63-1]")

    def check(self, dimension: str, requested: int, stage: str) -> None:
        limit = getattr(self, dimension)
        if requested > limit:
            raise AnalysisResourceError(dimension, limit, requested, stage)

    def tighten(self, values: dict | None) -> AnalysisLimits:
        values = normalize_limits({} if values is None else values)
        for key, value in values.items():
            if value > getattr(self, key):
                raise ConfigError(f"task resources.{key} cannot exceed the execution profile")
        return replace(self, **values)

    def evidence(self) -> dict:
        return {"schema": "wavebench.analysis_resources.v1", "estimator": "conservative.v2",
                "limits": asdict(self), "memory_limit_kind": "estimated_working_set"}


def normalize_limits(values: Any) -> dict[str, int]:
    if not isinstance(values, dict) or set(values) - set(AnalysisLimits.__dataclass_fields__):
        raise ConfigError("analysis resources must be a table of known resource limits")
    replace(AnalysisLimits(), **values)
    return dict(values)


def load_resource_limits(path: str | Path | None = None) -> AnalysisLimits:
    if path is None:
        return AnalysisLimits()
    try:
        file = Path(path)
        if file.stat().st_size > 65536:
            raise ConfigError("analysis resource profile exceeds 65536 bytes")
        values = tomllib.loads(file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read analysis resource profile: {exc}") from exc
    if values.pop("schema", None) != "wavebench.analysis_resources.v1":
        raise ConfigError("resource profile schema must be wavebench.analysis_resources.v1")
    return replace(AnalysisLimits(), **normalize_limits(values))


def check_static(operations: list[dict], limits: AnalysisLimits, *, metadata_files: int = 2) -> None:
    limits.check("max_operations", len(operations), "plan")
    files = metadata_files
    for index, op in enumerate(operations):
        stage = f"operations[{index}]"
        if op["op"] == "filter" and op["family"] == "fir":
            limits.check("max_fir_taps", op["numtaps"], stage)
            if op["mode"] == "zero_phase":
                limits.check("max_zero_phase_fir_taps", op["numtaps"], stage)
        if op["op"] == "psd":
            limits.check("max_fft_length", op["nfft"], stage)
        if op["op"] == "export":
            files += len(op["formats"])
        if op["op"] == "peaks":
            files += 2
    limits.check("max_output_files", files, "plan")


@dataclass
class AnalysisBudget:
    limits: AnalysisLimits
    work_units: int = 0
    output_bytes: int = 0
    output_files: int = 0

    def source(self, count: int, itemsize: int = 8) -> None:
        self.limits.check("max_input_samples", count, "source")
        self.limits.check("max_working_bytes", count * (2 * itemsize + 32) + 65536, "source")

    def stage(self, operation: dict, count: int, *, domain: str = "time", retained_bytes: int = 0) -> dict:
        op = operation["op"]
        # Input, output, masks, array expressions and backend workspace coexist.
        memory, work = retained_bytes + 128 * count + 65536, count
        if op == "fft":
            self.limits.check("max_fft_length", count, op)
            work = count * max(1, count.bit_length()) * 8
        elif op == "psd":
            nfft, segment = operation["nfft"], operation["nperseg"]
            self.limits.check("max_fft_length", nfft, op)
            k = max(0, 1 + (count - segment) // (segment - operation["noverlap"]))
            bins = nfft // 2 + 1
            memory += (1 if operation["average"] == "mean" else k) * (32 * segment + 64 * bins) + 64 * nfft
            work = k * nfft * max(1, nfft.bit_length()) * 8
        elif op == "filter":
            taps = operation.get("numtaps", 2 * operation.get("order", 1) + 1)
            passes = 2 if operation["mode"] == "zero_phase" else 1
            work = count * taps * passes
            if operation["family"] == "fir" and passes == 2:
                # Conservative across supported SciPy versions, including dense initial-state solves.
                memory += 32 * taps * taps + 128 * 3 * taps
                work += taps**3
        elif op == "smooth":
            work = count * operation["window_length"]
        elif op == "resample":
            out = (count * operation["up"] + operation["down"] - 1) // operation["down"]
            self.limits.check("max_input_samples", out, op)
            taps = 20 * max(operation["up"], operation["down"]) + 1
            memory += 64 * out + 32 * taps
            work = out * (taps // operation["up"] + 1)
        elif op in {"peaks", "spectral_quality"}:
            # Worst case admission before find_peaks allocates any candidates or properties.
            candidates = (count // 2) * (2 if operation.get("polarity") == "both" else 1)
            self.limits.check("max_peak_candidates", candidates, op)
            memory += 1024 * candidates
            work = count * max(1, count.bit_length())
            if op == "spectral_quality":
                # Explicit per-candidate integration masks are linear in spectrum length.
                work += count * (candidates + len(operation['harmonic_orders']) + 8)
        elif op == "export":
            cols = 4 if domain == "frequency" else 2
            expected = sum(count * cols * (8 if fmt == "npy" else 32) + 1024
                           for fmt in operation["formats"])
            self.limits.check("max_output_bytes", self.output_bytes + expected, op)
            self.limits.check("max_temp_bytes", max(count * cols * (8 if f == "npy" else 32) + 1024
                                                    for f in operation["formats"]), op)
        self.limits.check("max_working_bytes", memory, op)
        self.limits.check("max_work_units", self.work_units + work, op)
        self.work_units += work
        return {"estimated_working_bytes": memory, "work_units": work}

    def pending_file(self, size: int) -> None:
        self.limits.check("max_temp_bytes", size, "write")
        self.limits.check("max_output_bytes", self.output_bytes + size, "write")
        self.limits.check("max_output_files", self.output_files + 1, "write")

    def committed_file(self, size: int) -> None:
        self.output_bytes += size
        self.output_files += 1
