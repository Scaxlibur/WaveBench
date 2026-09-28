"""Offline planning for frequency-response remeasurement.

This module never opens an instrument.  It identifies reusable and missing grid
points so a subsequent run plan can set ``resume_from`` explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from wavebench.errors import ConfigError
from wavebench.services.frequency_response import load_frequency_response_points
from wavebench.services.frequency_response_evidence import case_id, plan_digest
from wavebench.services.run_plan import RunPlan, load_run_plan


RESUME_SCHEMA = "wavebench.frequency_response_resume.v1"


@dataclass(frozen=True)
class ResumeManifest:
    source_csv: Path
    plan_hash: str
    label: str
    reusable_cases: tuple[dict[str, Any], ...]
    pending_cases: tuple[dict[str, Any], ...]
    rejected_points: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RESUME_SCHEMA,
            "source_csv": str(self.source_csv),
            "plan_hash": self.plan_hash,
            "label": self.label,
            "reusable_cases": list(self.reusable_cases),
            "pending_cases": list(self.pending_cases),
            "rejected_points": list(self.rejected_points),
            "summary": {
                "reusable": len(self.reusable_cases),
                "pending": len(self.pending_cases),
                "rejected": len(self.rejected_points),
            },
        }

    def write(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
        return output


def build_frequency_response_resume(
    run_or_csv: str | Path,
    plan: RunPlan | str | Path,
    *,
    response_label: str | None = None,
) -> ResumeManifest:
    """Build a deterministic pending-point manifest from an existing CSV."""

    loaded_plan = load_run_plan(plan) if not isinstance(plan, RunPlan) else plan
    step = _select_step(loaded_plan, response_label)
    source = Path(run_or_csv)
    if source.is_dir():
        manifest = source / "frequency_responses.json"
        if manifest.exists():
            source = _select_csv_from_manifest(source, manifest, response_label)
        else:
            source = source / "frequency_response.csv"
    points = load_frequency_response_points(source)
    expected_hash = plan_digest(loaded_plan)
    by_case: dict[str, list[Any]] = {}
    by_fallback: dict[tuple[float | None, str], list[Any]] = {}
    for point in points:
        if point.plan_hash and point.plan_hash != expected_hash:
            raise ConfigError(
                f"resume response plan hash does not match the current plan: {point.plan_hash} != {expected_hash}"
            )
        if point.case_id:
            by_case.setdefault(point.case_id, []).append(point)
        by_fallback.setdefault(
            (point.requested_source_vpp, _number_key(point.requested_frequency_hz)), []
        ).append(point)

    amplitudes = step.fields.get("amplitudes_vpp") or [None]
    reusable: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for amplitude_index, requested_vpp in enumerate(amplitudes):
        for frequency_hz in step.fields["frequencies_hz"]:
            current_case = case_id(
                plan_name=loaded_plan.name,
                step_index=step.index,
                label=step.fields.get("label", f"frequency_response_{step.index:02d}"),
                frequency_hz=frequency_hz,
                requested_vpp=requested_vpp,
                reference_channel=step.fields["reference_channel"],
                response_channel=step.fields["response_channel"],
            )
            candidates = by_case.get(current_case, [])
            if not candidates:
                candidates = by_fallback.get(
                    (requested_vpp, _number_key(frequency_hz)), []
                )
            item = {
                "case_id": current_case,
                "amplitude_index": amplitude_index,
                "requested_vpp": requested_vpp,
                "requested_frequency_hz": frequency_hz,
            }
            if len(candidates) > 1:
                rejected.append(
                    {"case_id": current_case, "reason": "duplicate_existing_points", "count": len(candidates)}
                )
                pending.append(item)
                continue
            point = candidates[0] if candidates else None
            if point is not None and point.status == "ok" and point.usable_for_fit:
                reusable.append({**item, "source_index": point.index, "acquisition_id": point.acquisition_id})
            else:
                if point is not None:
                    rejected.append(
                        {
                            **item,
                            "source_index": point.index,
                            "status": point.status,
                            "reason": point.failure_reason or point.error or "not_reusable",
                        }
                    )
                pending.append(item)
    return ResumeManifest(
        source_csv=source,
        plan_hash=expected_hash,
        label=step.fields.get("label", f"frequency_response_{step.index:02d}"),
        reusable_cases=tuple(reusable),
        pending_cases=tuple(pending),
        rejected_points=tuple(rejected),
    )


def _select_step(plan: RunPlan, label: str | None):
    steps = [step for step in plan.steps if step.kind == "sweep.frequency_response"]
    if label is not None:
        steps = [step for step in steps if step.fields.get("label") == label]
    if len(steps) != 1:
        choices = ", ".join(
            str(step.fields.get("label", f"frequency_response_{step.index:02d}"))
            for step in steps
        )
        if not steps:
            raise ConfigError("run resume found no matching frequency-response step")
        raise ConfigError(f"run resume requires one response label; available: {choices}")
    return steps[0]


def _select_csv_from_manifest(run_dir: Path, manifest_path: Path, label: str | None) -> Path:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"frequency responses manifest is unreadable: {manifest_path}") from exc
    entries = document.get("responses") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise ConfigError(f"frequency responses manifest has no responses array: {manifest_path}")
    if label is None and len(entries) != 1:
        raise ConfigError("run resume requires --response for a multi-response run")
    selected = next(
        (entry for entry in entries if isinstance(entry, dict) and entry.get("label") == label),
        entries[0] if label is None and entries else None,
    )
    if not isinstance(selected, dict):
        raise ConfigError(f"frequency response {label!r} was not found in {manifest_path}")
    directory = Path(str(selected.get("directory", ".")))
    if not directory.is_absolute():
        directory = run_dir / directory
    return directory / str(selected.get("csv", "frequency_response.csv"))


def _number_key(value: float | None) -> str:
    return "none" if value is None else format(float(value), ".12g")


__all__ = ["RESUME_SCHEMA", "ResumeManifest", "build_frequency_response_resume"]
