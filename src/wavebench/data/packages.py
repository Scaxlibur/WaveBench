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

    @property
    def status(self) -> str:
        return str(self.run.get("status", "unknown"))

    @property
    def steps(self) -> list[dict[str, Any]]:
        value = self.run.get("steps", [])
        return value if isinstance(value, list) else []


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
    response_path = run_dir / "frequency_response.csv"
    response_rows: list[dict[str, str]] = []
    present_response_path: Path | None = None
    if response_path.exists():
        present_response_path = response_path
        with response_path.open(newline="", encoding="utf-8") as file:
            response_rows = [dict(row) for row in csv.DictReader(file)]
    fit_path = run_dir / "frequency_response_fit.json"
    present_fit_path: Path | None = fit_path if fit_path.exists() else None
    fit: dict[str, Any] | None = None
    fit_error: str | None = None
    if present_fit_path is not None:
        try:
            fit = _read_json_object(present_fit_path, label="frequency response fit JSON")
        except ConfigError as exc:
            fit_error = str(exc)
    calibration_csv_path = run_dir / "frequency_response_calibration.csv"
    calibration_rows: list[dict[str, str]] = []
    present_calibration_csv_path: Path | None = None
    if calibration_csv_path.exists():
        present_calibration_csv_path = calibration_csv_path
        with calibration_csv_path.open(newline="", encoding="utf-8") as file:
            calibration_rows = [dict(row) for row in csv.DictReader(file)]
    calibration_path = run_dir / "frequency_response_calibration.json"
    present_calibration_path: Path | None = calibration_path if calibration_path.exists() else None
    calibration: dict[str, Any] | None = None
    calibration_error: str | None = None
    if present_calibration_path is not None:
        try:
            calibration = _read_json_object(
                present_calibration_path, label="frequency response calibration JSON"
            )
        except ConfigError as exc:
            calibration_error = str(exc)
    return RunPackage(
        path=run_dir,
        run_json_path=run_json_path,
        run=run_data,
        summary_csv_path=present_summary_path,
        summary_rows=rows,
        frequency_response_csv_path=present_response_path,
        frequency_response_rows=response_rows,
        frequency_response_fit_path=present_fit_path,
        frequency_response_fit=fit,
        frequency_response_fit_error=fit_error,
        frequency_response_calibration_csv_path=present_calibration_csv_path,
        frequency_response_calibration_rows=calibration_rows,
        frequency_response_calibration_path=present_calibration_path,
        frequency_response_calibration=calibration,
        frequency_response_calibration_error=calibration_error,
    )


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
