from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
import json
from math import isfinite
from pathlib import Path
from typing import Any

import numpy as np

from wavebench.errors import ConfigError


FIT_METHODS = (
    "linear_log",
    "polynomial",
    "pchip",
    "smoothing_spline_db",
    "piecewise_chebyshev_db",
)

# These defaults are part of the on-disk evidence contract.  Keep them local to
# this module as well as in the evidence helper so that a FrequencyResponsePoint
# remains constructible by older callers that do not import the helper module.
CAPTURE_SYNC_GRADE = "waveforms_atomic_aux_best_effort"
REFERENCE_PLANE = "scope_input"
EVIDENCE_SCHEMA = "wavebench.frequency_response_evidence.v1"

BASE_CSV_FIELDS = (
    "index",
    "case_id",
    "acquisition_id",
    "capture_sync_grade",
    "requested_source_vpp",
    "requested_vpp",
    "reference_plane",
    "signal_level_evidence",
    "quality_metrics",
    "plan_hash",
    "amplitude_index",
    "requested_frequency_hz",
    "reference_frequency_hz",
    "response_frequency_hz",
    "reference_amplitude_peak_v",
    "response_amplitude_peak_v",
    "reference_vpp_v",
    "response_vpp_v",
    "gain_linear",
    "gain_db",
    "phase_wrapped_deg",
    "phase_unwrapped_deg",
    "baseline_gain_db",
    "baseline_phase_unwrapped_deg",
    "gain_linear_corrected",
    "gain_db_corrected",
    "phase_wrapped_corrected_deg",
    "phase_unwrapped_corrected_deg",
    "adaptive_level",
    "adaptive_parent_start_hz",
    "adaptive_parent_stop_hz",
    "quality_retry_count",
    "initial_warnings",
    "initial_capture_package",
    "initial_metadata_path",
    "retry_capture_package",
    "retry_metadata_path",
    "status",
    "failure_reason",
    "exclusion_reason",
    "warnings",
    "error",
    "capture_package",
    "metadata_path",
)


@dataclass(frozen=True)
class FrequencyResponsePoint:
    index: int
    requested_frequency_hz: float
    reference_frequency_hz: float | None
    response_frequency_hz: float | None
    reference_amplitude_peak_v: float | None
    response_amplitude_peak_v: float | None
    reference_vpp_v: float | None
    response_vpp_v: float | None
    gain_linear: float | None
    gain_db: float | None
    phase_wrapped_deg: float | None
    phase_unwrapped_deg: float | None
    status: str
    amplitude_index: int = 0
    requested_vpp: float | None = None
    baseline_gain_db: float | None = None
    baseline_phase_unwrapped_deg: float | None = None
    gain_linear_corrected: float | None = None
    gain_db_corrected: float | None = None
    phase_wrapped_corrected_deg: float | None = None
    phase_unwrapped_corrected_deg: float | None = None
    adaptive_level: int = 0
    adaptive_parent_start_hz: float | None = None
    adaptive_parent_stop_hz: float | None = None
    quality_retry_count: int = 0
    initial_warnings: tuple[str, ...] = ()
    initial_capture_package: str = ""
    initial_metadata_path: str = ""
    retry_capture_package: str = ""
    retry_metadata_path: str = ""
    warnings: tuple[str, ...] = ()
    error: str = ""
    capture_package: str = ""
    metadata_path: str = ""
    # Evidence fields are appended after the original fields intentionally: a
    # few third-party callers construct this dataclass positionally.  Appending
    # preserves that older positional calling convention while exposing the
    # richer provenance contract to new callers.
    case_id: str = ""
    acquisition_id: str = ""
    capture_sync_grade: str = CAPTURE_SYNC_GRADE
    requested_source_vpp: float | None = None
    reference_plane: str = REFERENCE_PLANE
    signal_level_evidence: dict[str, Any] = field(default_factory=dict)
    quality_metrics: dict[str, Any] = field(default_factory=dict)
    plan_hash: str = ""
    failure_reason: str = ""
    exclusion_reason: str = ""

    def __post_init__(self) -> None:
        """Normalize aliases and preserve an explicit reason for every unusable point.

        ``requested_vpp`` was the original public field.  New code should use
        ``requested_source_vpp`` because it names the physical reference plane
        unambiguously, but both fields are kept in each point/CSV row during the
        compatibility window.  The new field is authoritative when both are
        supplied; when only the legacy alias is supplied it seeds the new field.
        """

        requested_source = self.requested_source_vpp
        requested_alias = self.requested_vpp
        if requested_source is None:
            requested_source = requested_alias
        elif requested_alias is None or requested_alias != requested_source:
            # Keep the legacy alias as an actual alias, rather than allowing two
            # subtly different requested levels to enter fit grouping.
            requested_alias = requested_source
        object.__setattr__(self, "requested_source_vpp", requested_source)
        object.__setattr__(self, "requested_vpp", requested_alias)

        grade = str(self.capture_sync_grade or CAPTURE_SYNC_GRADE)
        plane = str(self.reference_plane or REFERENCE_PLANE)
        object.__setattr__(self, "capture_sync_grade", grade)
        object.__setattr__(self, "reference_plane", plane)

        evidence = self.signal_level_evidence
        if evidence is None:
            normalized_evidence: dict[str, Any] = {}
        elif isinstance(evidence, dict):
            normalized_evidence = dict(evidence)
        elif isinstance(evidence, str):
            try:
                parsed = json.loads(evidence)
            except json.JSONDecodeError:
                parsed = {}
            normalized_evidence = dict(parsed) if isinstance(parsed, dict) else {}
        else:
            try:
                normalized_evidence = dict(evidence)
            except (TypeError, ValueError) as exc:
                raise TypeError("signal_level_evidence must be a JSON object") from exc
        object.__setattr__(self, "signal_level_evidence", normalized_evidence)

        metrics = self.quality_metrics
        if metrics is None:
            normalized_metrics: dict[str, Any] = {}
        elif isinstance(metrics, dict):
            normalized_metrics = dict(metrics)
        elif isinstance(metrics, str):
            try:
                parsed_metrics = json.loads(metrics)
            except json.JSONDecodeError:
                parsed_metrics = {}
            normalized_metrics = dict(parsed_metrics) if isinstance(parsed_metrics, dict) else {}
        else:
            try:
                normalized_metrics = dict(metrics)
            except (TypeError, ValueError) as exc:
                raise TypeError("quality_metrics must be a JSON object") from exc
        object.__setattr__(self, "quality_metrics", normalized_metrics)

        failure = str(self.failure_reason or "").strip()
        error = str(self.error or "").strip()
        if self.status == "failed" and not failure:
            failure = error
        object.__setattr__(self, "failure_reason", failure)

        exclusion = str(self.exclusion_reason or "").strip()
        if self.status == "failed" and not exclusion:
            exclusion = failure or "failed_point"
        object.__setattr__(self, "exclusion_reason", exclusion)

    @property
    def usable_for_fit(self) -> bool:
        return (
            self.status != "failed"
            and self.gain_linear is not None
            and isfinite(self.gain_linear)
            and self.gain_linear > 0
        )

    @property
    def fit_exclusion_reason(self) -> str:
        """Return the stable reason used when this point is omitted from a fit."""

        if self.exclusion_reason:
            return self.exclusion_reason
        if self.failure_reason:
            return self.failure_reason
        if self.status == "failed":
            return self.error or "failed_point"
        if self.gain_linear is None:
            return "missing_gain"
        if not isfinite(self.gain_linear):
            return "non_finite_gain"
        if self.gain_linear <= 0:
            return "non_positive_gain"
        return "not_usable_for_fit"

    def as_csv_row(self, fit_values: dict[str, tuple[float | None, float | None]] | None = None) -> dict[str, object]:
        row: dict[str, object] = {
            "index": self.index,
            "case_id": self.case_id,
            "acquisition_id": self.acquisition_id,
            "capture_sync_grade": self.capture_sync_grade,
            "requested_source_vpp": self.requested_source_vpp,
            "requested_vpp": self.requested_vpp,
            "reference_plane": self.reference_plane,
            "signal_level_evidence": _json_object_text(self.signal_level_evidence),
            "quality_metrics": _json_object_text(self.quality_metrics),
            "plan_hash": self.plan_hash,
            "amplitude_index": self.amplitude_index,
            "requested_frequency_hz": self.requested_frequency_hz,
            "reference_frequency_hz": self.reference_frequency_hz,
            "response_frequency_hz": self.response_frequency_hz,
            "reference_amplitude_peak_v": self.reference_amplitude_peak_v,
            "response_amplitude_peak_v": self.response_amplitude_peak_v,
            "reference_vpp_v": self.reference_vpp_v,
            "response_vpp_v": self.response_vpp_v,
            "gain_linear": self.gain_linear,
            "gain_db": self.gain_db,
            "phase_wrapped_deg": self.phase_wrapped_deg,
            "phase_unwrapped_deg": self.phase_unwrapped_deg,
            "baseline_gain_db": self.baseline_gain_db,
            "baseline_phase_unwrapped_deg": self.baseline_phase_unwrapped_deg,
            "gain_linear_corrected": self.gain_linear_corrected,
            "gain_db_corrected": self.gain_db_corrected,
            "phase_wrapped_corrected_deg": self.phase_wrapped_corrected_deg,
            "phase_unwrapped_corrected_deg": self.phase_unwrapped_corrected_deg,
            "adaptive_level": self.adaptive_level,
            "adaptive_parent_start_hz": self.adaptive_parent_start_hz,
            "adaptive_parent_stop_hz": self.adaptive_parent_stop_hz,
            "quality_retry_count": self.quality_retry_count,
            "initial_warnings": " | ".join(self.initial_warnings),
            "initial_capture_package": self.initial_capture_package,
            "initial_metadata_path": self.initial_metadata_path,
            "retry_capture_package": self.retry_capture_package,
            "retry_metadata_path": self.retry_metadata_path,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "exclusion_reason": self.exclusion_reason,
            "warnings": " | ".join(self.warnings),
            "error": self.error,
            "capture_package": self.capture_package,
            "metadata_path": self.metadata_path,
        }
        for method, values in (fit_values or {}).items():
            row[f"fit_{method}_gain_linear"] = values[0]
            row[f"fit_{method}_residual"] = values[1]
        return row


def analyze_frequency_response_point(
    *,
    index: int,
    amplitude_index: int = 0,
    requested_vpp: float | None = None,
    requested_frequency_hz: float,
    reference_waveform: Any,
    response_waveform: Any,
    frequency_tolerance_ratio: float,
    min_signal_vpp: float = 0.02,
    capture_package: str,
    metadata_path: str,
    adaptive_level: int = 0,
    adaptive_parent_start_hz: float | None = None,
    adaptive_parent_stop_hz: float | None = None,
    case_id: str = "",
    acquisition_id: str = "",
    capture_sync_grade: str = CAPTURE_SYNC_GRADE,
    reference_plane: str = REFERENCE_PLANE,
    signal_level_evidence: dict[str, Any] | str | None = None,
    plan_hash: str = "",
    failure_reason: str = "",
    exclusion_reason: str = "",
) -> FrequencyResponsePoint:
    """Compute one transfer-function point from a simultaneous two-channel capture."""
    try:
        reference_phasor, reference_amplitude = _fit_sine_phasor(
            reference_waveform, requested_frequency_hz
        )
        response_phasor, response_amplitude = _fit_sine_phasor(
            response_waveform, requested_frequency_hz
        )
        if reference_amplitude <= _amplitude_floor(reference_waveform):
            raise ValueError("reference fundamental amplitude is zero or too small")
        if response_amplitude <= _amplitude_floor(response_waveform):
            raise ValueError("response fundamental amplitude is zero or too small")
        transfer = response_phasor / reference_phasor
        gain_linear = float(abs(transfer))
        if not isfinite(gain_linear) or gain_linear <= 0:
            raise ValueError("gain is not finite and positive")
        gain_db = float(20.0 * np.log10(gain_linear))
        phase_wrapped_deg = _wrap_phase_deg(float(np.degrees(np.angle(transfer))))
        reference_summary = _summary(
            reference_waveform, requested_frequency_hz, frequency_tolerance_ratio, min_signal_vpp
        )
        response_summary = _summary(
            response_waveform, requested_frequency_hz, frequency_tolerance_ratio, min_signal_vpp
        )
        warnings = _quality_warnings(reference_summary, "reference") + _quality_warnings(
            response_summary, "response"
        )
        quality_metrics = {
            "reference": _waveform_quality_metrics(reference_waveform),
            "response": _waveform_quality_metrics(response_waveform),
        }
        reference_vpp = _finite_or_none(reference_summary.get("voltage_vpp_v"))
        response_vpp = _finite_or_none(response_summary.get("voltage_vpp_v"))
        return FrequencyResponsePoint(
            index=index,
            requested_frequency_hz=requested_frequency_hz,
            reference_frequency_hz=_finite_or_none(reference_summary.get("frequency_estimate_hz")),
            response_frequency_hz=_finite_or_none(response_summary.get("frequency_estimate_hz")),
            reference_amplitude_peak_v=reference_amplitude,
            response_amplitude_peak_v=response_amplitude,
            reference_vpp_v=reference_vpp,
            response_vpp_v=response_vpp,
            gain_linear=gain_linear,
            gain_db=gain_db,
            phase_wrapped_deg=phase_wrapped_deg,
            phase_unwrapped_deg=None,
            status="warning" if warnings else "ok",
            amplitude_index=amplitude_index,
            requested_vpp=requested_vpp,
            adaptive_level=adaptive_level,
            adaptive_parent_start_hz=adaptive_parent_start_hz,
            adaptive_parent_stop_hz=adaptive_parent_stop_hz,
            warnings=tuple(warnings),
            capture_package=capture_package,
            metadata_path=metadata_path,
            case_id=case_id,
            acquisition_id=acquisition_id,
            capture_sync_grade=capture_sync_grade,
            reference_plane=reference_plane,
            signal_level_evidence=(
                signal_level_evidence
                if signal_level_evidence is not None
                else _default_signal_level_evidence(
                    requested_source_vpp=requested_vpp,
                    reference_vpp_v=reference_vpp,
                    response_vpp_v=response_vpp,
                    reference_waveform=reference_waveform,
                    response_waveform=response_waveform,
                    reference_plane=reference_plane,
                )
            ),
            quality_metrics=quality_metrics,
            plan_hash=plan_hash,
            failure_reason=failure_reason,
            exclusion_reason=exclusion_reason,
        )
    except Exception as exc:  # noqa: BLE001 - every point must remain auditable
        return FrequencyResponsePoint(
            index=index,
            requested_frequency_hz=requested_frequency_hz,
            reference_frequency_hz=None,
            response_frequency_hz=None,
            reference_amplitude_peak_v=None,
            response_amplitude_peak_v=None,
            reference_vpp_v=None,
            response_vpp_v=None,
            gain_linear=None,
            gain_db=None,
            phase_wrapped_deg=None,
            phase_unwrapped_deg=None,
            status="failed",
            amplitude_index=amplitude_index,
            requested_vpp=requested_vpp,
            adaptive_level=adaptive_level,
            adaptive_parent_start_hz=adaptive_parent_start_hz,
            adaptive_parent_stop_hz=adaptive_parent_stop_hz,
            error=f"{type(exc).__name__}: {exc}",
            capture_package=capture_package,
            metadata_path=metadata_path,
            case_id=case_id,
            acquisition_id=acquisition_id,
            capture_sync_grade=capture_sync_grade,
            reference_plane=reference_plane,
            signal_level_evidence=(
                signal_level_evidence
                if signal_level_evidence is not None
                else _default_signal_level_evidence(
                    requested_source_vpp=requested_vpp,
                    reference_vpp_v=None,
                    response_vpp_v=None,
                    reference_waveform=reference_waveform,
                    response_waveform=response_waveform,
                    reference_plane=reference_plane,
                )
            ),
            quality_metrics={},
            plan_hash=plan_hash,
            failure_reason=failure_reason,
            exclusion_reason=exclusion_reason,
        )


def failed_frequency_response_point(
    *,
    index: int,
    amplitude_index: int = 0,
    requested_vpp: float | None = None,
    requested_frequency_hz: float,
    error: Exception | str,
    adaptive_level: int = 0,
    adaptive_parent_start_hz: float | None = None,
    adaptive_parent_stop_hz: float | None = None,
    case_id: str = "",
    acquisition_id: str = "",
    capture_sync_grade: str = CAPTURE_SYNC_GRADE,
    reference_plane: str = REFERENCE_PLANE,
    signal_level_evidence: dict[str, Any] | str | None = None,
    quality_metrics: dict[str, Any] | str | None = None,
    plan_hash: str = "",
    failure_reason: str = "",
    exclusion_reason: str = "",
) -> FrequencyResponsePoint:
    text = str(error)
    if isinstance(error, Exception):
        text = f"{type(error).__name__}: {error}"
    return FrequencyResponsePoint(
        index=index,
        requested_frequency_hz=requested_frequency_hz,
        reference_frequency_hz=None,
        response_frequency_hz=None,
        reference_amplitude_peak_v=None,
        response_amplitude_peak_v=None,
        reference_vpp_v=None,
        response_vpp_v=None,
        gain_linear=None,
        gain_db=None,
        phase_wrapped_deg=None,
        phase_unwrapped_deg=None,
        status="failed",
        amplitude_index=amplitude_index,
        requested_vpp=requested_vpp,
        adaptive_level=adaptive_level,
        adaptive_parent_start_hz=adaptive_parent_start_hz,
        adaptive_parent_stop_hz=adaptive_parent_stop_hz,
        error=text,
        case_id=case_id,
        acquisition_id=acquisition_id,
        capture_sync_grade=capture_sync_grade,
        reference_plane=reference_plane,
        signal_level_evidence=signal_level_evidence,
        quality_metrics=quality_metrics or {},
        plan_hash=plan_hash,
        failure_reason=failure_reason or text,
        exclusion_reason=exclusion_reason or text,
    )


def unwrap_frequency_response_phase(points: list[FrequencyResponsePoint]) -> list[FrequencyResponsePoint]:
    """Unwrap each amplitude slice in frequency order.

    Adaptive points are acquired after their parent grid, so acquisition order is no
    longer frequency order.  Sorting here prevents those late points from creating
    fictitious 360-degree jumps while retaining failed evidence rows.
    """
    grouped: dict[int, list[FrequencyResponsePoint]] = {}
    for point in points:
        grouped.setdefault(point.amplitude_index, []).append(point)
    result: list[FrequencyResponsePoint] = []
    for amplitude_index in sorted(grouped):
        block: list[FrequencyResponsePoint] = []

        def flush() -> None:
            if not block:
                return
            unwrapped = np.degrees(np.unwrap(np.radians([point.phase_wrapped_deg for point in block])))
            result.extend(
                replace(point, phase_unwrapped_deg=float(value))
                for point, value in zip(block, unwrapped)
            )
            block.clear()

        for point in sorted(grouped[amplitude_index], key=lambda item: item.requested_frequency_hz):
            if point.status == "failed" or point.phase_wrapped_deg is None:
                flush()
                result.append(point)
            else:
                block.append(point)
        flush()
    return result


def response_gain_db(point: FrequencyResponsePoint) -> float | None:
    """Return the corrected gain when available, otherwise the raw measured gain."""
    return point.gain_db_corrected if point.gain_db_corrected is not None else point.gain_db


def response_phase_unwrapped_deg(point: FrequencyResponsePoint) -> float | None:
    """Return the corrected phase when available, otherwise the raw measured phase."""
    return (
        point.phase_unwrapped_corrected_deg
        if point.phase_unwrapped_corrected_deg is not None
        else point.phase_unwrapped_deg
    )


def ensure_fit_dependencies(fit: dict[str, Any] | None) -> None:
    methods = _fit_methods(fit)
    if not {"pchip", "smoothing_spline_db"}.intersection(methods):
        return
    try:
        from scipy.interpolate import PchipInterpolator  # noqa: F401
    except ImportError as exc:
        raise ConfigError(
            "frequency response PCHIP and smoothing-spline fits require the optional analysis dependency; "
            "install WaveBench with `.[analysis]`"
        ) from exc


def build_fit_document(
    points: list[FrequencyResponsePoint], fit: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, dict[int, tuple[float | None, float | None]]]]:
    if fit is None:
        return None, {}
    methods = _fit_methods(fit)
    degree = int(fit.get("polynomial_degree", 3))
    sample_count = int(fit.get("sample_count", 240))
    usable = sorted((point for point in points if point.usable_for_fit), key=lambda point: point.requested_frequency_hz)
    excluded = [
        {
            "index": point.index,
            "case_id": point.case_id,
            "frequency_hz": point.requested_frequency_hz,
            "requested_vpp": point.requested_source_vpp,
            "reason": point.fit_exclusion_reason,
            "failure_reason": point.failure_reason,
            "exclusion_reason": point.fit_exclusion_reason,
        }
        for point in points
        if not point.usable_for_fit
    ]
    document: dict[str, Any] = {
        "schema_version": 1,
        "x_transform": "log10(frequency_hz / Hz)",
        "valid_points": [],
        "excluded_points": excluded,
        "methods": {},
    }
    requested_amplitudes = sorted(
        {point.requested_vpp for point in usable if point.requested_vpp is not None}
    )
    if len(requested_amplitudes) > 1:
        selected_amplitude = requested_amplitudes[0]
        usable = [point for point in usable if point.requested_vpp == selected_amplitude]
        document["fit_amplitude_vpp"] = selected_amplitude
        document["fit_note"] = (
            "Multiple requested Vpp slices were captured; conventional one-dimensional fits use "
            "the lowest requested Vpp slice. Use frequency_response_calibration.json for the 2D model."
        )
    document["valid_points"] = [point.index for point in usable]
    fit_values: dict[str, dict[int, tuple[float | None, float | None]]] = {}
    if usable:
        document["valid_domain_hz"] = [
            usable[0].requested_frequency_hz,
            usable[-1].requested_frequency_hz,
        ]
    else:
        document["valid_domain_hz"] = None

    x = np.log10(np.asarray([point.requested_frequency_hz for point in usable], dtype=float))
    y = np.asarray(
        [
            point.gain_linear_corrected
            if point.gain_linear_corrected is not None
            else point.gain_linear
            for point in usable
        ],
        dtype=float,
    )
    for method in methods:
        result, values = _fit_method(
            method=method,
            x=x,
            y=y,
            points=usable,
            polynomial_degree=degree,
            sample_count=sample_count,
        )
        document["methods"][method] = result
        fit_values[method] = values
    return document, fit_values


def write_frequency_response_csv(
    path: str | Path,
    points: list[FrequencyResponsePoint],
    fit_values: dict[str, dict[int, tuple[float | None, float | None]]] | None = None,
) -> Path:
    output = Path(path)
    methods = tuple((fit_values or {}).keys())
    fieldnames = list(BASE_CSV_FIELDS)
    for method in methods:
        fieldnames.extend((f"fit_{method}_gain_linear", f"fit_{method}_residual"))
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for point in points:
            method_values = {
                method: (fit_values or {}).get(method, {}).get(point.index, (None, None))
                for method in methods
            }
            writer.writerow(point.as_csv_row(method_values))
    temporary.replace(output)
    return output


def load_frequency_response_points(path: str | Path) -> list[FrequencyResponsePoint]:
    """Load persisted response rows for an offline resume or re-analysis.

    Unknown/derived CSV columns are ignored, so files from older WaveBench
    versions remain usable.  The returned points retain failure evidence and are
    never modified on disk.
    """

    source = Path(path)
    if source.is_dir():
        source = source / "frequency_response.csv"
    if not source.exists():
        raise ConfigError(f"frequency response CSV not found: {source}")
    try:
        with source.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
    except OSError as exc:
        raise ConfigError(f"failed to read frequency response CSV: {source}: {exc}") from exc
    points: list[FrequencyResponsePoint] = []
    for row_index, row in enumerate(rows):
        try:
            frequency = _csv_float(row, "requested_frequency_hz")
        except ValueError as exc:
            raise ConfigError(f"frequency response CSV row {row_index} has no valid requested frequency") from exc
        status = str(row.get("status", "ok") or "ok").strip().lower()
        if status not in {"ok", "warning", "failed"}:
            status = "warning"
        points.append(
            FrequencyResponsePoint(
                index=_csv_int(row, "index", row_index),
                amplitude_index=_csv_int(row, "amplitude_index", 0),
                requested_vpp=_csv_optional_float(row, "requested_vpp"),
                requested_source_vpp=_csv_optional_float(row, "requested_source_vpp"),
                requested_frequency_hz=frequency,
                reference_frequency_hz=_csv_optional_float(row, "reference_frequency_hz"),
                response_frequency_hz=_csv_optional_float(row, "response_frequency_hz"),
                reference_amplitude_peak_v=_csv_optional_float(row, "reference_amplitude_peak_v"),
                response_amplitude_peak_v=_csv_optional_float(row, "response_amplitude_peak_v"),
                reference_vpp_v=_csv_optional_float(row, "reference_vpp_v"),
                response_vpp_v=_csv_optional_float(row, "response_vpp_v"),
                gain_linear=_csv_optional_float(row, "gain_linear"),
                gain_db=_csv_optional_float(row, "gain_db"),
                phase_wrapped_deg=_csv_optional_float(row, "phase_wrapped_deg"),
                phase_unwrapped_deg=_csv_optional_float(row, "phase_unwrapped_deg"),
                baseline_gain_db=_csv_optional_float(row, "baseline_gain_db"),
                baseline_phase_unwrapped_deg=_csv_optional_float(row, "baseline_phase_unwrapped_deg"),
                gain_linear_corrected=_csv_optional_float(row, "gain_linear_corrected"),
                gain_db_corrected=_csv_optional_float(row, "gain_db_corrected"),
                phase_wrapped_corrected_deg=_csv_optional_float(row, "phase_wrapped_corrected_deg"),
                phase_unwrapped_corrected_deg=_csv_optional_float(row, "phase_unwrapped_corrected_deg"),
                adaptive_level=_csv_int(row, "adaptive_level", 0),
                adaptive_parent_start_hz=_csv_optional_float(row, "adaptive_parent_start_hz"),
                adaptive_parent_stop_hz=_csv_optional_float(row, "adaptive_parent_stop_hz"),
                quality_retry_count=_csv_int(row, "quality_retry_count", 0),
                initial_warnings=_csv_tuple(row.get("initial_warnings")),
                initial_capture_package=str(row.get("initial_capture_package", "") or ""),
                initial_metadata_path=str(row.get("initial_metadata_path", "") or ""),
                retry_capture_package=str(row.get("retry_capture_package", "") or ""),
                retry_metadata_path=str(row.get("retry_metadata_path", "") or ""),
                status=status,
                failure_reason=str(row.get("failure_reason", "") or ""),
                exclusion_reason=str(row.get("exclusion_reason", "") or ""),
                warnings=_csv_tuple(row.get("warnings")),
                error=str(row.get("error", "") or ""),
                capture_package=str(row.get("capture_package", "") or ""),
                metadata_path=str(row.get("metadata_path", "") or ""),
                case_id=str(row.get("case_id", "") or ""),
                acquisition_id=str(row.get("acquisition_id", "") or ""),
                capture_sync_grade=str(
                    row.get("capture_sync_grade", CAPTURE_SYNC_GRADE) or CAPTURE_SYNC_GRADE
                ),
                reference_plane=str(row.get("reference_plane", REFERENCE_PLANE) or REFERENCE_PLANE),
                signal_level_evidence=_csv_json_object(row.get("signal_level_evidence")),
                quality_metrics=_csv_json_object(row.get("quality_metrics")),
                plan_hash=str(row.get("plan_hash", "") or ""),
            )
        )
    return points


def _csv_optional_float(row: dict[str, str], name: str) -> float | None:
    raw = row.get(name)
    if raw in (None, "", "None", "null"):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _csv_float(row: dict[str, str], name: str) -> float:
    value = _csv_optional_float(row, name)
    if value is None:
        raise ValueError(name)
    return value


def _csv_int(row: dict[str, str], name: str, default: int) -> int:
    raw = row.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _csv_tuple(raw: Any) -> tuple[str, ...]:
    if raw in (None, ""):
        return ()
    return tuple(item.strip() for item in str(raw).split("|") if item.strip())


def _csv_json_object(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return dict(value) if isinstance(value, dict) else {}


def write_fit_document(path: str | Path, document: dict[str, Any] | None) -> Path | None:
    if document is None:
        return None
    output = Path(path)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output)
    return output


def _fit_sine_phasor(waveform: Any, frequency_hz: float) -> tuple[complex, float]:
    times = np.asarray(getattr(waveform, "times_s"), dtype=float)
    voltages = np.asarray(getattr(waveform, "voltages_v"), dtype=float)
    if not isfinite(frequency_hz) or frequency_hz <= 0:
        raise ValueError("frequency must be finite and positive")
    if times.ndim != 1 or voltages.ndim != 1:
        raise ValueError("waveform time and voltage samples must be one-dimensional")
    if times.size != voltages.size:
        raise ValueError("waveform time and voltage sample counts must match")
    if times.size < 4:
        raise ValueError("need at least four waveform samples")
    if not np.all(np.isfinite(times)) or not np.all(np.isfinite(voltages)):
        raise ValueError("waveform samples must be finite")
    if np.any(np.diff(times) <= 0):
        raise ValueError("waveform time samples must be strictly increasing")

    time_origin = float(times[0])
    omega_times = 2.0 * np.pi * frequency_hz * (times - time_origin)
    design = np.column_stack((np.ones(times.size), np.sin(omega_times), np.cos(omega_times)))
    coefficients, _, rank, _ = np.linalg.lstsq(design, voltages, rcond=None)
    if rank < 3:
        raise ValueError("sine fit is rank deficient")
    local_phasor = complex(float(coefficients[1]), float(coefficients[2]))
    phasor = local_phasor * np.exp(-1j * 2.0 * np.pi * frequency_hz * time_origin)
    amplitude = float(abs(phasor))
    if not isfinite(amplitude):
        raise ValueError("sine fit amplitude is not finite")
    return phasor, amplitude


def _amplitude_floor(waveform: Any) -> float:
    values = np.asarray(getattr(waveform, "voltages_v"), dtype=float)
    finite = values[np.isfinite(values)]
    scale = float(np.max(np.abs(finite))) if finite.size else 0.0
    return max(1e-12, scale * 1e-12)


def _summary(
    waveform: Any,
    frequency_hz: float,
    tolerance_ratio: float,
    min_signal_vpp: float,
) -> dict[str, Any]:
    summary = waveform.summary(
        expected_frequency_hz=frequency_hz,
        frequency_tolerance_ratio=tolerance_ratio,
        min_signal_vpp=min_signal_vpp,
    )
    return summary if isinstance(summary, dict) else {}


def _quality_warnings(summary: dict[str, Any], channel: str) -> list[str]:
    raw = summary.get("quality_warnings", [])
    if isinstance(raw, list):
        return [f"{channel}: {item}" for item in raw if item]
    return [f"{channel}: {raw}"] if raw else []


def _waveform_quality_metrics(waveform: Any) -> dict[str, Any]:
    """Compute non-destructive quality telemetry from a captured waveform.

    These values are evidence, not automatic pass/fail gates.  Driver-provided
    quality warnings remain authoritative for frequency-lock and instrument-specific
    clipping checks.
    """

    values = np.asarray(getattr(waveform, "voltages_v"), dtype=float)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {"status": "unavailable", "sample_count": 0}
    centered = finite - float(np.mean(finite))
    peak = float(np.max(np.abs(centered)))
    rms = float(np.sqrt(np.mean(np.square(centered))))
    minimum = float(np.min(finite))
    maximum = float(np.max(finite))
    span = maximum - minimum
    tolerance = max(span * 1e-6, 1e-12)
    extrema_hits = np.count_nonzero(
        (np.abs(finite - minimum) <= tolerance) | (np.abs(finite - maximum) <= tolerance)
    )
    return {
        "status": "ok",
        "sample_count": int(finite.size),
        "rms_v": rms,
        "peak_ac_v": peak,
        "crest_factor": float(peak / rms) if rms > 0 else None,
        "vpp_v": span,
        "dc_offset_v": float(np.mean(finite)),
        "extrema_hit_fraction": float(extrema_hits / finite.size),
        "finite_fraction": float(finite.size / values.size) if values.size else 0.0,
    }


def _default_signal_level_evidence(
    *,
    requested_source_vpp: float | None,
    reference_vpp_v: float | None,
    response_vpp_v: float | None,
    reference_waveform: Any,
    response_waveform: Any,
    reference_plane: str,
) -> dict[str, Any]:
    """Build a conservative point-level level record for direct service callers.

    ``RunService`` adds the stable case/acquisition identifiers and rewrites this
    object with the same values after a real capture.  Keeping a useful default
    here means direct/offline analysis still carries the requested-versus-measured
    distinction instead of emitting an empty provenance field.
    """

    return {
        "schema": EVIDENCE_SCHEMA,
        "reference_plane": reference_plane,
        "requested_source_vpp": requested_source_vpp,
        "measured_reference_vpp": reference_vpp_v,
        "measured_response_vpp": response_vpp_v,
        "reference_channel": getattr(reference_waveform, "channel", None),
        "response_channel": getattr(response_waveform, "channel", None),
        "conversion": {"applied": False, "assumption": "none"},
    }


def _finite_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _json_object_text(value: dict[str, Any]) -> str:
    """Serialize an evidence object deterministically for a CSV cell."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _wrap_phase_deg(value: float) -> float:
    return float((value + 180.0) % 360.0 - 180.0)


def _fit_methods(fit: dict[str, Any] | None) -> tuple[str, ...]:
    if fit is None:
        return ()
    methods = fit.get("methods", FIT_METHODS)
    if not isinstance(methods, list):
        raise ConfigError("frequency response fit methods must be a list")
    normalized = tuple(str(method).strip().lower() for method in methods)
    if not normalized or any(method not in FIT_METHODS for method in normalized):
        choices = ", ".join(FIT_METHODS)
        raise ConfigError(f"frequency response fit methods must use: {choices}")
    if len(set(normalized)) != len(normalized):
        raise ConfigError("frequency response fit methods must not contain duplicates")
    return normalized


def _fit_method(
    *,
    method: str,
    x: np.ndarray,
    y: np.ndarray,
    points: list[FrequencyResponsePoint],
    polynomial_degree: int,
    sample_count: int,
) -> tuple[dict[str, Any], dict[int, tuple[float | None, float | None]]]:
    if x.size < 2:
        return _unavailable_fit(method, "at least two valid points are required")
    curve_x = np.linspace(float(x[0]), float(x[-1]), max(2, sample_count))
    try:
        if method == "linear_log":
            predicted = np.interp(x, x, y)
            curve_y = np.interp(curve_x, x, y)
            segments = _linear_segments(x, y)
            result = {
                "status": "ok",
                "display": "Log-frequency piecewise linear interpolation",
                "formula": "x = log10(f / Hz); G = m_i * x + b_i within segment i",
                "parameters": {"segments": segments},
            }
        elif method == "polynomial":
            if polynomial_degree < 1 or polynomial_degree > 5:
                return _unavailable_fit(method, "polynomial degree must be from 1 through 5")
            if x.size < polynomial_degree + 1:
                return _unavailable_fit(
                    method, f"degree {polynomial_degree} requires at least {polynomial_degree + 1} valid points"
                )
            coefficients = np.polyfit(x, y, polynomial_degree)
            predicted = np.polyval(coefficients, x)
            curve_y = np.polyval(coefficients, curve_x)
            terms = [
                {"power": polynomial_degree - offset, "coefficient": float(value)}
                for offset, value in enumerate(coefficients)
            ]
            result = {
                "status": "ok",
                "display": f"Degree-{polynomial_degree} polynomial in log frequency",
                "formula": "x = log10(f / Hz); G = sum(c_k * x^k)",
                "parameters": {"degree": polynomial_degree, "terms": terms},
            }
        elif method == "pchip":
            try:
                from scipy.interpolate import PchipInterpolator
            except ImportError as exc:  # pragma: no cover - guarded in run check
                return _unavailable_fit(method, f"optional dependency unavailable: {exc}")
            interpolator = PchipInterpolator(x, y, extrapolate=False)
            predicted = np.asarray(interpolator(x), dtype=float)
            curve_y = np.asarray(interpolator(curve_x), dtype=float)
            result = {
                "status": "ok",
                "display": "PCHIP shape-preserving cubic interpolation",
                "formula": "x = log10(f / Hz); G_i(x) = c3*(x-x_i)^3 + c2*(x-x_i)^2 + c1*(x-x_i) + c0",
                "parameters": {
                    "segments": _pchip_segments(interpolator),
                },
            }
        elif method == "smoothing_spline_db":
            predicted_db, curve_db, alpha = _smoothing_spline_db(x, y, curve_x)
            predicted = np.power(10.0, predicted_db / 20.0)
            curve_y = np.power(10.0, curve_db / 20.0)
            result = {
                "status": "ok",
                "display": "Smoothing spline in dB over log frequency",
                "formula": "x = log10(f / Hz); G_dB = S(x); G = 10^(G_dB / 20)",
                "parameters": {"smoothing_alpha": alpha, "domain": "dB"},
            }
        elif method == "piecewise_chebyshev_db":
            predicted_db, curve_db, segments = _piecewise_chebyshev_db(x, y, curve_x)
            predicted = np.power(10.0, predicted_db / 20.0)
            curve_y = np.power(10.0, curve_db / 20.0)
            result = {
                "status": "ok",
                "display": "Piecewise Chebyshev approximation in dB",
                "formula": "x = log10(f / Hz); G_dB = sum(c_k * T_k(t)) within segment; G = 10^(G_dB / 20)",
                "parameters": {"domain": "dB", "segments": segments},
            }
        else:  # pragma: no cover - normalized before dispatch
            return _unavailable_fit(method, "unsupported method")
    except Exception as exc:  # noqa: BLE001 - optional fit must not discard measurements
        return _unavailable_fit(method, f"{type(exc).__name__}: {exc}")

    predicted = np.asarray(predicted, dtype=float)
    curve_y = np.asarray(curve_y, dtype=float)
    if not np.all(np.isfinite(predicted)) or not np.all(np.isfinite(curve_y)):
        return _unavailable_fit(method, "fit produced non-finite values")
    residuals = y - predicted
    result["metrics"] = _fit_metrics(y, predicted)
    result["curve"] = [
        {"frequency_hz": float(10.0**x_value), "gain_linear": float(y_value)}
        for x_value, y_value in zip(curve_x, curve_y)
    ]
    values = {
        point.index: (float(value), float(residual))
        for point, value, residual in zip(points, predicted, residuals)
    }
    return result, values


def _smoothing_spline_db(
    x: np.ndarray, y_linear: np.ndarray, curve_x: np.ndarray, *, alpha: float = 0.1
) -> tuple[np.ndarray, np.ndarray, float]:
    """Fit a conservative spline in dB, scaling the penalty to the observed variance."""
    try:
        from scipy.interpolate import UnivariateSpline
    except ImportError as exc:  # pragma: no cover - guarded in run check
        raise ConfigError("smoothing_spline_db requires WaveBench with `.[analysis]`") from exc
    y_db = 20.0 * np.log10(y_linear)
    if x.size < 4:
        raise ValueError("smoothing spline requires at least four valid points")
    variance = float(np.var(y_db))
    smoothing = max(0.0, alpha * x.size * variance)
    spline = UnivariateSpline(x, y_db, k=min(3, x.size - 1), s=smoothing)
    return (
        np.asarray(spline(x), dtype=float),
        np.asarray(spline(curve_x), dtype=float),
        alpha,
    )


def _piecewise_chebyshev_db(
    x: np.ndarray, y_linear: np.ndarray, curve_x: np.ndarray, *, segment_count: int = 8,
    degree: int = 3,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    """Approximate dB gain by deployable low-order Chebyshev pieces in log-frequency."""
    if x.size < 2:
        raise ValueError("Chebyshev approximation requires at least two valid points")
    y_db = 20.0 * np.log10(y_linear)
    count = min(segment_count, x.size - 1)
    edges = np.linspace(float(x[0]), float(x[-1]), count + 1)
    predictions = np.empty_like(x)
    curve_predictions = np.empty_like(curve_x)
    segments: list[dict[str, Any]] = []
    for index, (start, stop) in enumerate(zip(edges[:-1], edges[1:])):
        in_measurement = (x >= start) & ((x < stop) if index < count - 1 else (x <= stop))
        sample_x = x[in_measurement]
        sample_count = max(degree + 1, sample_x.size)
        fit_x = np.linspace(start, stop, sample_count)
        fit_y = np.interp(fit_x, x, y_db)
        polynomial = np.polynomial.Chebyshev.fit(
            fit_x, fit_y, deg=min(degree, sample_count - 1), domain=[start, stop]
        )
        predictions[in_measurement] = polynomial(sample_x)
        in_curve = (curve_x >= start) & ((curve_x < stop) if index < count - 1 else (curve_x <= stop))
        curve_predictions[in_curve] = polynomial(curve_x[in_curve])
        segments.append(
            {
                "x_start": float(start),
                "x_stop": float(stop),
                "coefficients": [float(value) for value in polynomial.coef],
            }
        )
    return predictions, curve_predictions, segments


def _unavailable_fit(method: str, reason: str) -> tuple[dict[str, Any], dict[int, tuple[float | None, float | None]]]:
    return {
        "status": "unavailable",
        "display": method,
        "reason": reason,
        "curve": [],
    }, {}


def _linear_segments(x: np.ndarray, y: np.ndarray) -> list[dict[str, float]]:
    segments: list[dict[str, float]] = []
    for index in range(x.size - 1):
        slope = float((y[index + 1] - y[index]) / (x[index + 1] - x[index]))
        segments.append(
            {
                "x_start": float(x[index]),
                "x_stop": float(x[index + 1]),
                "gain_start": float(y[index]),
                "slope": slope,
                "intercept": float(y[index] - slope * x[index]),
            }
        )
    return segments


def _pchip_segments(interpolator: Any) -> list[dict[str, Any]]:
    return [
        {
            "x_start": float(interpolator.x[index]),
            "x_stop": float(interpolator.x[index + 1]),
            "coefficients": [float(value) for value in interpolator.c[:, index]],
        }
        for index in range(len(interpolator.x) - 1)
    ]


def _fit_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float | None]:
    residual = actual - predicted
    rmse = float(np.sqrt(np.mean(np.square(residual))))
    mae = float(np.mean(np.abs(residual)))
    total = float(np.sum(np.square(actual - np.mean(actual))))
    if total <= 0:
        r_squared = 1.0 if np.allclose(residual, 0.0, rtol=0.0, atol=1e-12) else None
    else:
        r_squared = float(1.0 - np.sum(np.square(residual)) / total)
    return {"rmse": rmse, "mae": mae, "r_squared": r_squared}
