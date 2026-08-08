"""Offline comparison of persisted WaveBench frequency-response runs.

This module deliberately has no instrument or numerical-analysis dependency.
It compares the CSV/JSON artifacts produced by :mod:`wavebench.data.packages`
and returns a JSON-serialisable document suitable for a CLI or report.

The first package is the reference package; every following package is compared
against it independently.  A stable ``case_id`` is preferred for alignment.
For older artifacts which pre-date case identifiers, the requested frequency
and requested source Vpp form a deterministic fallback identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from wavebench.data.packages import (
    FrequencyResponsePackage,
    RunPackage,
    load_run_package,
)


COMPARE_SCHEMA = "wavebench.frequency_response_compare.v1"


class RunCompareError(ValueError):
    """Raised when comparison inputs cannot be interpreted safely."""


@dataclass(frozen=True)
class ComparisonPoint:
    """One aligned point and its candidate-minus-reference differences."""

    case_id: str
    reference: dict[str, Any] | None
    candidate: dict[str, Any] | None
    gain_db_delta: float | None = None
    phase_deg_delta: float | None = None
    identity: str = "case_id"
    gain_within_tolerance: bool | None = None
    phase_within_tolerance: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "identity": self.identity,
            "reference": self.reference,
            "candidate": self.candidate,
            "delta": {
                "gain_db": self.gain_db_delta,
                "phase_deg": self.phase_deg_delta,
            },
            "within_tolerance": {
                "gain_db": self.gain_within_tolerance,
                "phase_deg": self.phase_within_tolerance,
            },
        }


@dataclass(frozen=True)
class RunComparison:
    """Comparison of one candidate response with the reference response."""

    reference: dict[str, Any]
    candidate: dict[str, Any]
    status: str
    points: tuple[ComparisonPoint, ...] = ()
    missing: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {"reference": (), "candidate": ()}
    )
    duplicates: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {"reference": (), "candidate": ()}
    )
    incompatible: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        matched = sum(
            point.reference is not None and point.candidate is not None
            for point in self.points
        )
        delta_count = sum(
            point.gain_db_delta is not None or point.phase_deg_delta is not None
            for point in self.points
            if point.reference is not None and point.candidate is not None
        )
        out_of_tolerance = sum(
            point.gain_within_tolerance is False
            or point.phase_within_tolerance is False
            for point in self.points
        )
        return {
            "reference": self.reference,
            "candidate": self.candidate,
            "status": self.status,
            "points": [point.as_dict() for point in self.points],
            "missing": {
                "reference": list(self.missing.get("reference", ())),
                "candidate": list(self.missing.get("candidate", ())),
            },
            "duplicates": {
                "reference": list(self.duplicates.get("reference", ())),
                "candidate": list(self.duplicates.get("candidate", ())),
            },
            "incompatible": list(self.incompatible),
            "warnings": list(self.warnings),
            "summary": {
                "matched": matched,
                "delta_points": delta_count,
                "out_of_tolerance": out_of_tolerance,
                "within_tolerance": (
                    out_of_tolerance == 0
                    if any(
                        point.gain_within_tolerance is not None
                        or point.phase_within_tolerance is not None
                        for point in self.points
                    )
                    else None
                ),
                "missing_reference": len(self.missing.get("reference", ())),
                "missing_candidate": len(self.missing.get("candidate", ())),
                "duplicate_reference": len(self.duplicates.get("reference", ())),
                "duplicate_candidate": len(self.duplicates.get("candidate", ())),
            },
        }


@dataclass(frozen=True)
class CompareResult:
    """Top-level comparison result.

    ``as_dict`` is the stable wire representation.  ``to_json`` is provided so
    callers can persist the result without knowing the dataclass internals.
    """

    reference: dict[str, Any]
    runs: tuple[dict[str, Any], ...]
    comparisons: tuple[RunComparison, ...]
    errors: tuple[dict[str, Any], ...] = ()

    @property
    def schema_version(self) -> str:
        return COMPARE_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        comparisons = [comparison.as_dict() for comparison in self.comparisons]
        return {
            "schema_version": COMPARE_SCHEMA,
            "reference": self.reference,
            "runs": list(self.runs),
            "comparisons": comparisons,
            # ``candidates`` is a harmless compatibility alias for early
            # consumers which used that term before the result schema settled.
            "candidates": comparisons,
            "errors": list(self.errors),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent, ensure_ascii=False)

    # A small Mapping-like convenience keeps CLI code readable while retaining
    # an explicit result type for library callers.
    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


@dataclass(frozen=True)
class _PointRow:
    key: str
    fallback_key: str | None
    row: dict[str, Any]
    identity: str


@dataclass(frozen=True)
class _ResponseView:
    label: str
    path: Path
    run_id: str
    metadata: dict[str, Any]
    rows: tuple[_PointRow, ...]


def compare_run_packages(
    paths: Sequence[str | Path | RunPackage | Mapping[str, Any]],
    response_labels: Sequence[str | None] | Mapping[Any, str | None] | str | None = None,
    output_path: str | Path | None = None,
    *,
    gain_tolerance_db: float | None = None,
    phase_tolerance_deg: float | None = None,
) -> dict[str, Any]:
    """Compare two or more run packages offline.

    ``paths[0]`` is the reference.  Each later item is compared independently
    with it.  ``response_labels`` may be a parallel sequence, a mapping keyed
    by path/index, or one selector applied to every package.  The function
    returns a dictionary for straightforward JSON/CLI use; use
    :func:`compare_run_packages_result` when a dataclass is preferred.
    """

    return compare_run_packages_result(
        paths,
        response_labels=response_labels,
        gain_tolerance_db=gain_tolerance_db,
        phase_tolerance_deg=phase_tolerance_deg,
        output_path=output_path,
    ).as_dict()


def compare_run_packages_result(
    paths: Sequence[str | Path | RunPackage | Mapping[str, Any]],
    response_labels: Sequence[str | None] | Mapping[Any, str | None] | str | None = None,
    *,
    gain_tolerance_db: float | None = None,
    phase_tolerance_deg: float | None = None,
    output_path: str | Path | None = None,
) -> CompareResult:
    """Typed implementation behind :func:`compare_run_packages`."""

    if isinstance(paths, (str, Path, RunPackage, Mapping)):
        paths = [paths]  # type: ignore[list-item]
    items = list(paths)
    if len(items) < 2:
        raise RunCompareError("at least two run packages are required")
    if gain_tolerance_db is not None and gain_tolerance_db < 0:
        raise RunCompareError("gain_tolerance_db must be non-negative")
    if phase_tolerance_deg is not None and phase_tolerance_deg < 0:
        raise RunCompareError("phase_tolerance_deg must be non-negative")

    views: list[_ResponseView | None] = []
    errors: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        selector = _selector_for(response_labels, index, item)
        try:
            views.append(_load_response_view(item, selector))
        except Exception as exc:  # artifact errors belong in the report
            views.append(None)
            errors.append(
                {
                    "index": index,
                    "path": _item_label(item),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    reference_view = views[0]
    if reference_view is None:
        reference = {"index": 0, "path": _item_label(items[0]), "status": "unavailable"}
    else:
        reference = _view_identity(reference_view, index=0)

    run_metadata = [
        _view_identity(view, index=index)
        if view is not None
        else {"index": index, "path": _item_label(item), "status": "unavailable"}
        for index, (item, view) in enumerate(zip(items, views))
    ]

    comparisons: list[RunComparison] = []
    if reference_view is not None:
        for index, candidate_view in enumerate(views[1:], start=1):
            if candidate_view is None:
                comparisons.append(
                    RunComparison(
                        reference=reference,
                        candidate=run_metadata[index],
                        status="unavailable",
                        warnings=("candidate response could not be loaded",),
                    )
                )
                continue
            comparisons.append(
                _compare_views(
                    reference_view,
                    candidate_view,
                    reference=reference,
                    candidate=run_metadata[index],
                    gain_tolerance_db=gain_tolerance_db,
                    phase_tolerance_deg=phase_tolerance_deg,
                )
            )

    result = CompareResult(
        reference=reference,
        runs=tuple(run_metadata),
        comparisons=tuple(comparisons),
        errors=tuple(errors),
    )
    if output_path is not None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(result.to_json() + "\n", encoding="utf-8")
        temporary.replace(output)
    return result


# Short aliases make the service discoverable without duplicating behavior.
compare_runs = compare_run_packages
compare_run_artifacts = compare_run_packages


def _selector_for(
    selectors: Sequence[str | None] | Mapping[Any, str | None] | str | None,
    index: int,
    item: Any,
) -> str | None:
    if selectors is None:
        return None
    if isinstance(selectors, str):
        return selectors
    if isinstance(selectors, Mapping):
        for key in (index, str(index), item, str(item) if isinstance(item, (str, Path)) else None):
            if key is not None:
                try:
                    if key in selectors:
                        return selectors[key]
                except TypeError:
                    continue
        return None
    if index < len(selectors):
        return selectors[index]
    return None


def _load_response_view(
    item: str | Path | RunPackage | Mapping[str, Any], selector: str | None
) -> _ResponseView:
    if isinstance(item, RunPackage):
        package = item
    elif isinstance(item, Mapping):
        package = _run_package_from_mapping(item)
    else:
        package = load_run_package(item)
    response = package.select_frequency_response(selector)
    if isinstance(package, _MemoryRunPackage):
        return _view_from_memory(package, response)
    return _view_from_package(package, response)


def _view_from_package(package: RunPackage, response: FrequencyResponsePackage) -> _ResponseView:
    metadata = _response_metadata(package, response)
    rows = tuple(_normalise_row(row, index=index) for index, row in enumerate(response.rows))
    return _ResponseView(
        label=response.label,
        path=package.path,
        run_id=_run_id(package),
        metadata=metadata,
        rows=rows,
    )


def _run_package_from_mapping(value: Mapping[str, Any]) -> RunPackage:
    """Build a lightweight package from a mapping for offline callers/tests.

    A mapping may contain ``path``, ``run`` and either ``rows`` or
    ``frequency_response_rows``.  This avoids forcing callers that already
    loaded JSON to round-trip through temporary files.
    """

    path = Path(str(value.get("path", "<memory>")))
    run = value.get("run", value)
    if not isinstance(run, Mapping):
        run = {}
    rows = value.get("rows", value.get("frequency_response_rows", []))
    if not isinstance(rows, list):
        rows = list(rows) if isinstance(rows, Iterable) and not isinstance(rows, (str, bytes)) else []
    label = str(value.get("label", "frequency_response"))
    response = _MemoryResponse(label=label, rows=[dict(row) for row in rows if isinstance(row, Mapping)])
    return _MemoryRunPackage(path=path, run=dict(run), response=response)


@dataclass(frozen=True)
class _MemoryResponse:
    label: str
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class _MemoryRunPackage:
    path: Path
    run: dict[str, Any]
    response: _MemoryResponse

    def select_frequency_response(self, label: str | None = None) -> _MemoryResponse:
        if label is not None and label != self.response.label:
            raise RunCompareError(f"frequency response {label!r} was not found")
        return self.response


def _view_from_memory(package: _MemoryRunPackage, response: _MemoryResponse) -> _ResponseView:
    return _ResponseView(
        label=response.label,
        path=package.path,
        run_id=str(package.run.get("run_id", package.path.name)),
        metadata=_response_metadata_from_values(package.run, {}, response.rows),
        rows=tuple(_normalise_row(row, index=index) for index, row in enumerate(response.rows)),
    )


def _view_identity(view: _ResponseView, *, index: int) -> dict[str, Any]:
    metadata = dict(view.metadata)
    return {
        "index": index,
        "path": str(view.path),
        "run_id": view.run_id,
        "label": view.label,
        "status": "ok",
        "metadata": metadata,
        "point_count": len(view.rows),
    }


def _compare_views(
    reference_view: _ResponseView,
    candidate_view: _ResponseView,
    *,
    reference: dict[str, Any],
    candidate: dict[str, Any],
    gain_tolerance_db: float | None,
    phase_tolerance_deg: float | None,
) -> RunComparison:
    incompatible = _compatibility_issues(reference_view.metadata, candidate_view.metadata)
    # Keep explicit identifiers separate from the legacy fallback key.  This
    # makes the provenance visible in the result and prevents a fallback key
    # from being mislabeled as an authoritative case ID.
    ref_index, _ = _index_rows(reference_view.rows, explicit_only=True)
    cand_index, _ = _index_rows(candidate_view.rows, explicit_only=True)
    _, ref_dupes = _index_rows(reference_view.rows)
    _, cand_dupes = _index_rows(candidate_view.rows)

    points: list[ComparisonPoint] = []
    matched_ref: set[int] = set()
    matched_cand: set[int] = set()

    # First align explicit case IDs.  This is the authoritative identity.
    for key in sorted(set(ref_index) & set(cand_index)):
        ref_rows = ref_index[key]
        cand_rows = cand_index[key]
        if len(ref_rows) == 1 and len(cand_rows) == 1:
            ri, ci = ref_rows[0], cand_rows[0]
            matched_ref.add(ri)
            matched_cand.add(ci)
            points.append(
                _comparison_point(
                    reference_view.rows[ri],
                    candidate_view.rows[ci],
                    identity="case_id",
                    gain_tolerance_db=gain_tolerance_db,
                    phase_tolerance_deg=phase_tolerance_deg,
                )
            )

    # Older rows may lack case_id.  Align remaining rows by frequency/Vpp.
    ref_fallback, cand_fallback = _fallback_index(reference_view.rows), _fallback_index(candidate_view.rows)
    for key in sorted(set(ref_fallback) & set(cand_fallback)):
        ref_rows = [i for i in ref_fallback[key] if i not in matched_ref]
        cand_rows = [i for i in cand_fallback[key] if i not in matched_cand]
        # A differing explicit case ID is meaningful evidence of a changed
        # requested case.  Fall back only when at least one side is an older
        # row without an explicit case identifier.
        if (
            len(ref_rows) == 1
            and len(cand_rows) == 1
            and (
                reference_view.rows[ref_rows[0]].identity != "case_id"
                or candidate_view.rows[cand_rows[0]].identity != "case_id"
            )
        ):
            ri, ci = ref_rows[0], cand_rows[0]
            matched_ref.add(ri)
            matched_cand.add(ci)
            points.append(
                _comparison_point(
                    reference_view.rows[ri],
                    candidate_view.rows[ci],
                    identity="frequency_vpp",
                    gain_tolerance_db=gain_tolerance_db,
                    phase_tolerance_deg=phase_tolerance_deg,
                )
            )

    # Keep deterministic ordering by requested frequency, Vpp, then row index.
    points.sort(key=lambda point: _point_sort_key(point.case_id, point.reference, point.candidate))
    missing_ref = tuple(
        _row_key(reference_view.rows[i])
        for i in range(len(reference_view.rows))
        if i not in matched_ref
    )
    missing_cand = tuple(_row_key(candidate_view.rows[i]) for i in range(len(candidate_view.rows)) if i not in matched_cand)
    duplicates = {"reference": tuple(sorted(ref_dupes)), "candidate": tuple(sorted(cand_dupes))}

    warnings: list[str] = []
    if missing_ref or missing_cand:
        warnings.append("frequency/Vpp grid differs between runs")
    if duplicates["reference"] or duplicates["candidate"]:
        warnings.append("duplicate point identities prevent unambiguous alignment")

    if incompatible:
        status = "incompatible"
    elif any(
        point.gain_within_tolerance is False or point.phase_within_tolerance is False
        for point in points
    ):
        status = "out_of_tolerance"
    elif duplicates["reference"] or duplicates["candidate"]:
        status = "duplicate"
    elif missing_ref or missing_cand:
        status = "partial"
    elif any(
        point.gain_within_tolerance is False or point.phase_within_tolerance is False
        for point in points
    ):
        status = "out_of_tolerance"
    else:
        status = "ok"

    return RunComparison(
        reference=reference,
        candidate=candidate,
        status=status,
        points=tuple(points),
        missing={"reference": missing_ref, "candidate": missing_cand},
        duplicates=duplicates,
        incompatible=tuple(incompatible),
        warnings=tuple(warnings),
    )


def _comparison_point(
    reference: _PointRow,
    candidate: _PointRow,
    *,
    identity: str,
    gain_tolerance_db: float | None,
    phase_tolerance_deg: float | None,
) -> ComparisonPoint:
    gain_ref = _number(_first(reference.row, "gain_db_corrected", "gain_db"))
    gain_cand = _number(_first(candidate.row, "gain_db_corrected", "gain_db"))
    phase_ref, phase_ref_kind = _phase_value(reference.row)
    phase_cand, phase_cand_kind = _phase_value(candidate.row)
    gain_delta = gain_cand - gain_ref if gain_ref is not None and gain_cand is not None else None
    phase_delta = _phase_difference(phase_cand, phase_ref, phase_ref_kind, phase_cand_kind)
    return ComparisonPoint(
        case_id=reference.key or candidate.key,
        reference=_public_row(reference.row),
        candidate=_public_row(candidate.row),
        gain_db_delta=gain_delta,
        phase_deg_delta=phase_delta,
        identity=identity,
        gain_within_tolerance=(
            abs(gain_delta) <= gain_tolerance_db
            if gain_delta is not None and gain_tolerance_db is not None
            else None
        ),
        phase_within_tolerance=(
            abs(phase_delta) <= phase_tolerance_deg
            if phase_delta is not None and phase_tolerance_deg is not None
            else None
        ),
    )


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    frequency = _number(_first(row, "requested_frequency_hz", "frequency_hz", "frequency", "freq_hz"))
    requested_vpp = _number(_first(row, "requested_source_vpp", "requested_vpp", "requested_vpp_v"))
    gain = _number(_first(row, "gain_db_corrected", "gain_db"))
    phase, phase_kind = _phase_value(row)
    return {
        "case_id": _text(row.get("case_id")) or None,
        "frequency_hz": frequency,
        "requested_vpp": requested_vpp,
        "gain_db": gain,
        "phase_deg": phase,
        "phase_kind": phase_kind,
        "reference_vpp_v": _number(row.get("reference_vpp_v")),
        "response_vpp_v": _number(row.get("response_vpp_v")),
        "status": _text(row.get("status")) or "unknown",
        "warnings": _split_text(row.get("warnings")),
        "error": _text(row.get("error")) or None,
    }


def _normalise_row(row: Mapping[str, Any], *, index: int) -> _PointRow:
    data = {str(key): value for key, value in row.items()}
    explicit = _text(_first(data, "case_id", "caseId"))
    frequency = _number(_first(data, "requested_frequency_hz", "frequency_hz", "frequency", "freq_hz"))
    requested_vpp = _number(
        _first(data, "requested_source_vpp", "requested_vpp", "requested_vpp_v", "source_vpp_v")
    )
    fallback = _fallback_key(frequency, requested_vpp)
    key = explicit or fallback or f"row:{index}"
    return _PointRow(key=key, fallback_key=fallback, row=data, identity="case_id" if explicit else "frequency_vpp")


def _index_rows(
    rows: Sequence[_PointRow], *, explicit_only: bool = False
) -> tuple[dict[str, list[int]], set[str]]:
    index: dict[str, list[int]] = {}
    for position, row in enumerate(rows):
        if explicit_only and row.identity != "case_id":
            continue
        index.setdefault(row.key, []).append(position)
    duplicates = {key for key, values in index.items() if len(values) > 1}
    return index, duplicates


def _fallback_index(rows: Sequence[_PointRow]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for position, row in enumerate(rows):
        if row.fallback_key is not None:
            index.setdefault(row.fallback_key, []).append(position)
    return index


def _row_key(row: _PointRow) -> str:
    return row.key


def _fallback_key(frequency: float | None, requested_vpp: float | None) -> str | None:
    if frequency is None:
        return None
    return f"frequency={_canonical_number(frequency)}|requested_vpp={_canonical_number(requested_vpp)}"


def _canonical_number(value: float | None) -> str:
    if value is None:
        return "none"
    return format(value, ".12g")


def _point_sort_key(key: str, reference: Mapping[str, Any] | None, candidate: Mapping[str, Any] | None) -> tuple[float, float, str]:
    row = reference or candidate or {}
    return (
        _number(row.get("frequency_hz")) or math.inf,
        _number(row.get("requested_vpp")) or math.inf,
        key,
    )


def _phase_value(row: Mapping[str, Any]) -> tuple[float | None, str]:
    for name, kind in (
        ("phase_unwrapped_corrected_deg", "unwrapped_corrected"),
        ("phase_unwrapped_deg", "unwrapped"),
        ("phase_wrapped_corrected_deg", "wrapped_corrected"),
        ("phase_wrapped_deg", "wrapped"),
        ("phase_deg", "generic"),
        ("phase", "generic"),
    ):
        value = _number(row.get(name))
        if value is not None:
            return value, kind
    return None, "none"


def _phase_difference(
    candidate: float | None, reference: float | None, reference_kind: str, candidate_kind: str
) -> float | None:
    if candidate is None or reference is None:
        return None
    delta = candidate - reference
    if not ("unwrapped" in reference_kind and "unwrapped" in candidate_kind):
        delta = (delta + 180.0) % 360.0 - 180.0
    return delta


def _compatibility_issues(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for field_name in ("reference_channel", "response_channel", "reference_plane", "capture_sync_grade"):
        left, right = reference.get(field_name), candidate.get(field_name)
        if left not in (None, "") and right not in (None, "") and str(left) != str(right):
            issues.append({"field": field_name, "reference": left, "candidate": right, "reason": "mismatch"})
    left_hash, right_hash = reference.get("plan_hash"), candidate.get("plan_hash")
    if left_hash not in (None, "") and right_hash not in (None, "") and str(left_hash) != str(right_hash):
        issues.append({"field": "plan_hash", "reference": left_hash, "candidate": right_hash, "reason": "mismatch"})
    return issues


def _response_metadata(package: RunPackage, response: FrequencyResponsePackage) -> dict[str, Any]:
    return _response_metadata_from_values(package.run, response.manifest_entry, response.rows)


def _response_metadata_from_values(
    run: Mapping[str, Any], entry: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    evidence = entry.get("evidence") if isinstance(entry.get("evidence"), Mapping) else {}
    first_evidence = {}
    for row in rows:
        raw = row.get("signal_level_evidence") or row.get("evidence")
        if isinstance(raw, Mapping):
            first_evidence = raw
            break
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                parsed = {}
            if isinstance(parsed, Mapping):
                first_evidence = parsed
                break
    response_step = _response_step(run, entry)
    run_provenance = run.get("provenance") if isinstance(run.get("provenance"), Mapping) else {}
    values: dict[str, Any] = {}
    for name in ("reference_channel", "response_channel", "reference_plane", "capture_sync_grade", "plan_hash"):
        value = entry.get(name)
        if value in (None, ""):
            value = evidence.get(name)
        if value in (None, ""):
            value = first_evidence.get(name)
        if value in (None, ""):
            value = response_step.get(name)
        if value in (None, ""):
            value = run_provenance.get(name)
        if value in (None, "") and isinstance(run_provenance.get("frequency_response"), Mapping):
            value = run_provenance["frequency_response"].get(name)
        if value not in (None, ""):
            values[name] = value
    # signal_level_evidence nests the plane in newer artifacts.
    if "reference_plane" not in values:
        nested = first_evidence.get("reference_plane")
        if nested not in (None, ""):
            values["reference_plane"] = nested
    return values


def _response_step(run: Mapping[str, Any], entry: Mapping[str, Any]) -> Mapping[str, Any]:
    wanted_label = entry.get("label")
    steps = run.get("steps")
    if not isinstance(steps, list):
        return {}
    for step in steps:
        if not isinstance(step, Mapping) or step.get("kind") != "sweep.frequency_response":
            continue
        artifact = step.get("artifact")
        response = artifact.get("frequency_response") if isinstance(artifact, Mapping) else None
        if not isinstance(response, Mapping):
            continue
        if wanted_label is None or response.get("label") == wanted_label:
            return response
    return {}


def _run_id(package: RunPackage) -> str:
    for key in ("run_id", "id", "uuid"):
        value = package.run.get(key)
        if value not in (None, ""):
            return str(value)
    return package.path.name


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _split_text(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = _text(value)
    return [item.strip() for item in text.split("|") if item.strip()] if text else []


def _item_label(item: Any) -> str:
    if isinstance(item, RunPackage):
        return str(item.path)
    if isinstance(item, Mapping):
        return str(item.get("path", "<memory>"))
    return str(item)


# Public serialization alias for callers that receive a CompareResult.
def serialize_compare_result(result: CompareResult | Mapping[str, Any], *, indent: int = 2) -> str:
    value = result.as_dict() if isinstance(result, CompareResult) else dict(result)
    return json.dumps(value, indent=indent, ensure_ascii=False)


__all__ = [
    "COMPARE_SCHEMA",
    "CompareResult",
    "ComparisonPoint",
    "RunComparison",
    "RunCompareError",
    "compare_run_artifacts",
    "compare_run_packages",
    "compare_run_packages_result",
    "compare_runs",
    "serialize_compare_result",
]
