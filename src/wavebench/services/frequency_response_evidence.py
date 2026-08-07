"""Stable, offline evidence helpers for frequency-response runs.

The helpers in this module deliberately do not know about instrument drivers.  They
only canonicalise the already-normalised run-plan fields and attach provenance to
capture metadata.  This keeps case identity stable across reruns while allowing the
physical acquisition identity to remain unique for every capture attempt.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any


EVIDENCE_SCHEMA = "wavebench.frequency_response_evidence.v1"
CAPTURE_SYNC_GRADE = "waveforms_atomic_aux_best_effort"


def canonical_json(value: Any) -> str:
    """Return deterministic JSON suitable for a digest."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any, *, length: int = 16) -> str:
    """Hash a JSON-compatible value with a short, human-safe prefix."""

    return sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def plan_digest(plan: Any) -> str:
    """Build a digest from normalised plan semantics, excluding its local path."""

    steps = []
    for step in getattr(plan, "steps", ()):
        fields = dict(step.fields)
        # ``resume_from`` selects an existing evidence source; it does not change
        # the requested measurement grid or signal semantics.  Excluding it lets
        # a resumed plan reuse points produced by its original plan.
        fields.pop("resume_from", None)
        steps.append({"index": step.index, "kind": step.kind, "fields": fields})
    return digest(
        {
            "name": getattr(plan, "name", ""),
            "label": getattr(plan, "label", ""),
            "safety": _as_dict(getattr(plan, "safety", None)),
            "restore": _as_dict(getattr(plan, "restore", None)),
            "steps": steps,
        }
    )


def case_id(
    *,
    plan_name: str,
    step_index: int,
    label: str,
    frequency_hz: float,
    requested_vpp: float | None,
    reference_channel: int,
    response_channel: int,
) -> str:
    """Return a stable identifier for a requested response grid point."""

    payload = {
        "plan_name": plan_name,
        "step_index": step_index,
        "label": label,
        "requested_frequency_hz": float(frequency_hz),
        "requested_vpp": None if requested_vpp is None else float(requested_vpp),
        "reference_channel": int(reference_channel),
        "response_channel": int(response_channel),
    }
    return f"case-{digest(payload, length=20)}"


def acquisition_id(package_dir: str | Path, *, label: str = "") -> str:
    """Return an id for one physical capture package.

    The package directory is generated uniquely by WaveBench, so this id remains
    stable when metadata is re-annotated and differs for an autoscale retry.
    """

    payload = {"package": str(Path(package_dir).resolve()), "label": label}
    return f"acq-{digest(payload, length=20)}"


def signal_level_evidence(
    *,
    requested_source_vpp: float | None,
    reference_vpp_v: float | None,
    response_vpp_v: float | None,
    reference_channel: int,
    response_channel: int,
    reference_plane: str = "scope_input",
) -> dict[str, Any]:
    """Describe requested and measured levels without assuming a load conversion."""

    return {
        "schema": EVIDENCE_SCHEMA,
        "reference_plane": reference_plane,
        "requested_source_vpp": requested_source_vpp,
        "measured_reference_vpp": reference_vpp_v,
        "measured_response_vpp": response_vpp_v,
        "reference_channel": reference_channel,
        "response_channel": response_channel,
        "conversion": {"applied": False, "assumption": "none"},
    }


def annotate_capture_metadata(
    metadata_path: str | Path,
    *,
    case: str,
    acquisition: str,
    requested_frequency_hz: float,
    requested_source_vpp: float | None,
    reference_channel: int,
    response_channel: int,
    reference_vpp_v: float | None = None,
    response_vpp_v: float | None = None,
    plan_hash: str = "",
    retry_of: str | None = None,
) -> dict[str, Any]:
    """Attach response evidence to a capture metadata file atomically.

    Fake captures and older packages may contain an empty JSON object; those are
    upgraded in place with the same schema and remain readable by older loaders.
    """

    path = Path(metadata_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "case_id": case,
        "acquisition_id": acquisition,
        "capture_sync_grade": CAPTURE_SYNC_GRADE,
        "plan_hash": plan_hash,
        "requested_frequency_hz": float(requested_frequency_hz),
        "requested_source_vpp": requested_source_vpp,
        "signal_level": signal_level_evidence(
            requested_source_vpp=requested_source_vpp,
            reference_vpp_v=reference_vpp_v,
            response_vpp_v=response_vpp_v,
            reference_channel=reference_channel,
            response_channel=response_channel,
        ),
    }
    if retry_of:
        evidence["retry_of_acquisition_id"] = retry_of
    raw["evidence"] = evidence
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return evidence


def _as_dict(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "__dict__"):
        return {key: _as_dict(item) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {str(key): _as_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_dict(item) for item in value]
    return value
