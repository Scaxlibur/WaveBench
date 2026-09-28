from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wavebench.errors import ConfigError


@dataclass(frozen=True)
class CaptureChannel:
    channel: int
    header: dict[str, Any]
    summary: dict[str, Any]
    files: dict[str, str]


@dataclass(frozen=True)
class CapturePackage:
    path: Path
    metadata_path: Path
    metadata: dict[str, Any]
    channels: list[CaptureChannel]

    @property
    def operation(self) -> dict[str, Any]:
        value = self.metadata.get("operation", {})
        return value if isinstance(value, dict) else {}

    @property
    def instrument(self) -> dict[str, Any]:
        value = self.metadata.get("instrument", {})
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class FrequencyResponsePackage:
    """One independently auditable frequency-response result within a run."""

    label: str
    step_index: int | None
    directory: Path
    csv_path: Path | None
    rows: list[dict[str, str]]
    fit_path: Path | None
    fit: dict[str, Any] | None
    fit_error: str | None
    baseline_path: Path | None
    baseline: dict[str, Any] | None
    baseline_error: str | None
    calibration_csv_path: Path | None
    calibration_rows: list[dict[str, str]]
    calibration_path: Path | None
    calibration: dict[str, Any] | None
    calibration_error: str | None
    manifest_entry: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunPackage:
    path: Path
    run_json_path: Path
    run: dict[str, Any]
    summary_csv_path: Path | None
    summary_rows: list[dict[str, str]]
    frequency_response_csv_path: Path | None = None
    frequency_response_rows: list[dict[str, str]] = field(default_factory=list)
    frequency_response_fit_path: Path | None = None
    frequency_response_fit: dict[str, Any] | None = None
    frequency_response_fit_error: str | None = None
    frequency_response_calibration_csv_path: Path | None = None
    frequency_response_calibration_rows: list[dict[str, str]] = field(default_factory=list)
    frequency_response_calibration_path: Path | None = None
    frequency_response_calibration: dict[str, Any] | None = None
    frequency_response_calibration_error: str | None = None
    frequency_responses_manifest_path: Path | None = None
    frequency_responses: list[FrequencyResponsePackage] = field(default_factory=list)

    @property
    def status(self) -> str:
        return str(self.run.get("status", "unknown"))

    @property
    def steps(self) -> list[dict[str, Any]]:
        value = self.run.get("steps", [])
        return value if isinstance(value, list) else []

    def select_frequency_response(self, label: str | None = None) -> FrequencyResponsePackage:
        """Select one response, requiring an explicit label for a multi-response run."""
        if label is not None:
            for response in self.frequency_responses:
                if response.label == label:
                    return response
            choices = ", ".join(response.label for response in self.frequency_responses) or "(none)"
            raise ConfigError(f"frequency response {label!r} was not found; available: {choices}")
        if len(self.frequency_responses) == 1:
            return self.frequency_responses[0]
        if not self.frequency_responses:
            raise ConfigError("run package has no frequency response CSV")
        choices = ", ".join(response.label for response in self.frequency_responses)
        raise ConfigError(f"run has multiple frequency responses; specify --response. Available: {choices}")


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"{label} not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a JSON object: {path}")
    return value


def load_capture_package(path: str | Path) -> CapturePackage:
    package_dir = Path(path)
    if not package_dir.exists():
        raise ConfigError(f"capture package not found: {package_dir}")
    if not package_dir.is_dir():
        raise ConfigError(f"capture package must be a directory: {package_dir}")
    metadata_path = package_dir / "metadata.json"
    metadata = _read_json_object(metadata_path, label="capture metadata")
    channels = _capture_channels(metadata)
    if not channels:
        raise ConfigError(f"capture metadata has no waveform channels: {metadata_path}")
    return CapturePackage(
        path=package_dir,
        metadata_path=metadata_path,
        metadata=metadata,
        channels=channels,
    )


def _capture_channels(metadata: dict[str, Any]) -> list[CaptureChannel]:
    if isinstance(metadata.get("channels"), dict):
        file_map = metadata.get("files", {})
        if not isinstance(file_map, dict):
            file_map = {}
        channels: list[CaptureChannel] = []
        for raw_channel, payload in sorted(metadata["channels"].items(), key=lambda item: int(item[0])):
            channel = int(raw_channel)
            channel_payload = payload if isinstance(payload, dict) else {}
            files = file_map.get(str(channel), {})
            channels.append(
                CaptureChannel(
                    channel=channel,
                    header=_dict_or_empty(channel_payload.get("header")),
                    summary=_dict_or_empty(channel_payload.get("summary")),
                    files=_dict_or_empty(files),
                )
            )
        return channels

    waveform = metadata.get("waveform")
    if isinstance(waveform, dict):
        summary = _dict_or_empty(waveform.get("summary"))
        operation = _dict_or_empty(metadata.get("operation"))
        channel = summary.get("channel", operation.get("channel"))
        if channel is None:
            raise ConfigError("capture metadata waveform is missing channel")
        return [
            CaptureChannel(
                channel=int(channel),
                header=_dict_or_empty(waveform.get("header")),
                summary=summary,
                files=_dict_or_empty(metadata.get("files")),
            )
        ]
    return []


def load_run_package(path: str | Path) -> RunPackage:
    run_dir = Path(path)
    if not run_dir.exists():
        raise ConfigError(f"run package not found: {run_dir}")
    if not run_dir.is_dir():
        raise ConfigError(f"run package must be a directory: {run_dir}")
    run_json_path = run_dir / "run.json"
    run_data = _read_json_object(run_json_path, label="run.json")
    summary_path = run_dir / "summary.csv"
    rows: list[dict[str, str]] = []
    present_summary_path: Path | None = None
    if summary_path.exists():
        present_summary_path = summary_path
        with summary_path.open(newline="", encoding="utf-8") as file:
            rows = [dict(row) for row in csv.DictReader(file)]
    manifest_path = run_dir / "frequency_responses.json"
    responses = _load_frequency_responses(run_dir, run_data, manifest_path if manifest_path.exists() else None)
    primary = responses[0] if responses else None
    fallback_calibration_csv = run_dir / "frequency_response_calibration.csv"
    fallback_calibration_rows = _read_csv_rows(fallback_calibration_csv)
    fallback_calibration_path, fallback_calibration, fallback_calibration_error = _read_optional_json(
        run_dir / "frequency_response_calibration.json", "frequency response calibration JSON"
    )
    return RunPackage(
        path=run_dir,
        run_json_path=run_json_path,
        run=run_data,
        summary_csv_path=present_summary_path,
        summary_rows=rows,
        frequency_response_csv_path=primary.csv_path if primary else None,
        frequency_response_rows=primary.rows if primary else [],
        frequency_response_fit_path=primary.fit_path if primary else None,
        frequency_response_fit=primary.fit if primary else None,
        frequency_response_fit_error=primary.fit_error if primary else None,
        frequency_response_calibration_csv_path=(
            primary.calibration_csv_path if primary else fallback_calibration_csv if fallback_calibration_csv.exists() else None
        ),
        frequency_response_calibration_rows=(
            primary.calibration_rows if primary else fallback_calibration_rows
        ),
        frequency_response_calibration_path=primary.calibration_path if primary else fallback_calibration_path,
        frequency_response_calibration=primary.calibration if primary else fallback_calibration,
        frequency_response_calibration_error=primary.calibration_error if primary else fallback_calibration_error,
        frequency_responses_manifest_path=manifest_path if manifest_path.exists() else None,
        frequency_responses=responses,
    )


def _load_frequency_responses(
    run_dir: Path, run_data: dict[str, Any], manifest_path: Path | None
) -> list[FrequencyResponsePackage]:
    if manifest_path is not None:
        manifest = _read_json_object(manifest_path, label="frequency responses manifest")
        entries = manifest.get("responses")
        if not isinstance(entries, list):
            raise ConfigError(f"frequency responses manifest has no responses array: {manifest_path}")
        responses: list[FrequencyResponsePackage] = []
        labels: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ConfigError(f"frequency responses manifest contains a non-object entry: {manifest_path}")
            label = entry.get("label")
            directory = entry.get("directory")
            if not isinstance(label, str) or not label.strip() or not isinstance(directory, str):
                raise ConfigError(f"frequency responses manifest entry requires label and directory: {manifest_path}")
            if label in labels:
                raise ConfigError(f"frequency responses manifest has duplicate label {label!r}: {manifest_path}")
            labels.add(label)
            step_index = entry.get("step_index")
            responses.append(
                _load_frequency_response_package(
                    run_dir=run_dir,
                    label=label,
                    step_index=step_index if isinstance(step_index, int) else None,
                    directory=_manifest_directory(run_dir, directory, manifest_path),
                    entry=entry,
                )
            )
        return responses

    legacy_csv = run_dir / "frequency_response.csv"
    if not legacy_csv.exists():
        return []
    label, step_index = _legacy_response_identity(run_data)
    return [
        _load_frequency_response_package(
            run_dir=run_dir,
            label=label,
            step_index=step_index,
            directory=run_dir,
            entry={},
        )
    ]


def _load_frequency_response_package(
    *,
    run_dir: Path,
    label: str,
    step_index: int | None,
    directory: Path,
    entry: dict[str, Any],
) -> FrequencyResponsePackage:
    csv_path = _artifact_file(directory, entry.get("csv"), "frequency_response.csv")
    rows = _read_csv_rows(csv_path)
    fit_path, fit, fit_error = _read_optional_json(
        _artifact_file(directory, entry.get("fit_json"), "frequency_response_fit.json"),
        "frequency response fit JSON",
    )
    baseline_path, baseline, baseline_error = _read_optional_json(
        _artifact_file(directory, entry.get("baseline_json"), "frequency_response_baseline.json"),
        "frequency response baseline JSON",
    )
    calibration_csv_path = _artifact_file(
        directory, entry.get("calibration_csv"), "frequency_response_calibration.csv"
    )
    calibration_rows = _read_csv_rows(calibration_csv_path)
    calibration_path, calibration, calibration_error = _read_optional_json(
        _artifact_file(directory, entry.get("calibration_json"), "frequency_response_calibration.json"),
        "frequency response calibration JSON",
    )
    return FrequencyResponsePackage(
        label=label,
        step_index=step_index,
        directory=directory,
        csv_path=csv_path if csv_path.exists() else None,
        rows=rows,
        fit_path=fit_path,
        fit=fit,
        fit_error=fit_error,
        baseline_path=baseline_path,
        baseline=baseline,
        baseline_error=baseline_error,
        calibration_csv_path=calibration_csv_path if calibration_csv_path.exists() else None,
        calibration_rows=calibration_rows,
        calibration_path=calibration_path,
        calibration=calibration,
        calibration_error=calibration_error,
        manifest_entry=dict(entry),
    )


def _artifact_file(directory: Path, raw: Any, default_name: str) -> Path:
    if isinstance(raw, str) and raw.strip():
        candidate = Path(raw)
        return candidate if candidate.is_absolute() else directory / candidate
    return directory / default_name


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as file:
        return [dict(row) for row in csv.DictReader(file)]


def _read_optional_json(path: Path, label: str) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None, None
    try:
        return path, _read_json_object(path, label=label), None
    except ConfigError as exc:
        return path, None, str(exc)


def _manifest_directory(run_dir: Path, raw: str, manifest_path: Path) -> Path:
    candidate = Path(raw)
    path = candidate if candidate.is_absolute() else run_dir / candidate
    try:
        path.resolve().relative_to(run_dir.resolve())
    except ValueError as exc:
        raise ConfigError(f"frequency responses manifest directory escapes run directory: {manifest_path}") from exc
    return path


def _legacy_response_identity(run_data: dict[str, Any]) -> tuple[str, int | None]:
    steps = run_data.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict) or step.get("kind") != "sweep.frequency_response":
                continue
            fields = step.get("fields")
            if isinstance(fields, dict):
                label = fields.get("label")
                if isinstance(label, str) and label.strip():
                    return label, step.get("index") if isinstance(step.get("index"), int) else None
            index = step.get("index")
            if isinstance(index, int):
                return f"frequency_response_{index:02d}", index
    return "frequency_response", None


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
