from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wavebench.errors import ensure_error_envelope
from wavebench.instruments.source_extensions import SOURCE_OPERATION_ARTIFACT_SCHEMA
from wavebench.services.run_plan import RunPlan
from wavebench.services.source_state import RestorableSourceState


@dataclass(frozen=True)
class RunStepRecord:
    index: int
    kind: str
    status: str
    fields: dict[str, Any]
    artifact: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "status": self.status,
            "fields": self.fields,
            "artifact": self.artifact,
        }


def write_step_record(steps_dir: Path, record: RunStepRecord) -> None:
    safe_kind = record.kind.replace(".", "_")
    path = steps_dir / f"{record.index:02d}_{safe_kind}.json"
    path.write_text(
        json.dumps(record.as_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _validated_source_operations(
    source_operations: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Accept only real, schema-labelled Source V2 operation artifacts.

    The namespace stays absent for all V1 runs.  Feature-specific V2 operation
    code owns the rest of each artifact's shape, but it cannot accidentally
    insert an arbitrary unlabelled dictionary at the run root.
    """

    if source_operations is None:
        return None
    if not isinstance(source_operations, list) or any(
        not isinstance(item, dict) for item in source_operations
    ):
        raise TypeError("source_operations must be a list of operation artifact objects")
    if not source_operations:
        return None
    for artifact in source_operations:
        if artifact.get("schema") != SOURCE_OPERATION_ARTIFACT_SCHEMA:
            raise ValueError("source operation artifact has an unsupported schema")
        operation = artifact.get("operation")
        if (
            not isinstance(operation, str)
            or not operation.startswith("source.")
            or operation.strip() != operation
        ):
            raise ValueError("source operation artifact must have a trimmed source.* operation")
    return source_operations


def write_run_files(
    *,
    plan: RunPlan,
    run_json_path: Path,
    summary_csv_path: Path,
    status: str,
    records: list[RunStepRecord],
    error: dict[str, Any] | None,
    restore_state: list[RestorableSourceState] | None = None,
    restore_error: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    source_operations: list[dict[str, Any]] | None = None,
) -> None:
    run_data: dict[str, Any] = {
        "status": status,
        "experiment": {"name": plan.name, "label": plan.label},
        "plan": str(plan.path),
        "steps": [record.as_dict() for record in records],
    }
    if restore_state is not None:
        snapshots = [state.as_dict() for state in restore_state]
        channels = [state.channel for state in restore_state]
        run_data["restore"] = {
            "source_state": True,
            "source_state_scope": "basic",
            "source_channels": channels,
            "snapshots": snapshots,
            "status": "failed" if restore_error is not None else "ok",
        }
        if len(restore_state) == 1:
            run_data["restore"]["source_channel"] = restore_state[0].channel
            run_data["restore"]["snapshot"] = restore_state[0].as_dict()
        if restore_error is not None:
            run_data["restore"]["error"] = ensure_error_envelope(
                restore_error,
                default_code="restore_failed",
                default_exit_code=2,
            )
    elif plan.restore.source_state:
        run_data["restore"] = {
            "source_state": True,
            "source_state_scope": "basic",
            "source_channels": list(plan.restore.source_channels),
            "status": "not_started",
        }
        if len(plan.restore.source_channels) == 1:
            run_data["restore"]["source_channel"] = plan.restore.source_channels[0]
    if error is not None:
        run_data["error"] = ensure_error_envelope(error, default_exit_code=2)
    if provenance is not None:
        run_data["provenance"] = provenance
    # Keep the V2 namespace absent until an actual Source V2 operation has a
    # typed artifact to place in it.  In particular, this must not alter the
    # byte representation of existing V1 run artifacts.
    validated_source_operations = _validated_source_operations(source_operations)
    if validated_source_operations is not None:
        run_data["source_operations"] = validated_source_operations
    run_json_path.write_text(
        json.dumps(run_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with summary_csv_path.open("w", newline="", encoding="utf-8") as file:
        fieldnames = [
            "index",
            "kind",
            "status",
            "package",
            "metadata",
            "quality_status",
            "quality_warnings",
            "recovered",
            "expect_status",
            "expect_failures",
            "expect_fft_status",
            "expect_fft_failures",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "index": record.index,
                    "kind": record.kind,
                    "status": record.status,
                    "package": record.artifact.get("package", ""),
                    "metadata": record.artifact.get("metadata", ""),
                    "quality_status": record.artifact.get("quality", {}).get("status", ""),
                    "quality_warnings": " | ".join(
                        record.artifact.get("quality", {}).get("warnings", [])
                    ),
                    "recovered": "yes" if "quality_recovery" in record.artifact else "",
                    "expect_status": record.artifact.get("expect", {}).get("status", ""),
                    "expect_failures": " | ".join(
                        record.artifact.get("expect", {}).get("failures", [])
                    ),
                    "expect_fft_status": record.artifact.get("expect_fft", {}).get("status", ""),
                    "expect_fft_failures": " | ".join(
                        record.artifact.get("expect_fft", {}).get("failures", [])
                    ),
                }
            )
