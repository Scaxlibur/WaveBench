from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from wavebench.services.frequency_response import (
    CAPTURE_SYNC_GRADE,
    FrequencyResponsePoint,
    REFERENCE_PLANE,
    build_fit_document,
    failed_frequency_response_point,
    write_frequency_response_csv,
    analyze_frequency_response_point,
)
from wavebench.services.frequency_response_evidence import (
    acquisition_id,
    annotate_capture_metadata,
    case_id,
)


def _valid_point(**changes: object) -> FrequencyResponsePoint:
    values: dict[str, object] = {
        "index": 0,
        "requested_frequency_hz": 1_000.0,
        "reference_frequency_hz": 1_000.0,
        "response_frequency_hz": 1_000.0,
        "reference_amplitude_peak_v": 0.5,
        "response_amplitude_peak_v": 1.0,
        "reference_vpp_v": 1.0,
        "response_vpp_v": 2.0,
        "gain_linear": 2.0,
        "gain_db": 6.020599913,
        "phase_wrapped_deg": 0.0,
        "phase_unwrapped_deg": 0.0,
        "status": "ok",
    }
    values.update(changes)
    return FrequencyResponsePoint(**values)


def test_requested_source_vpp_and_legacy_alias_stay_in_sync() -> None:
    legacy = _valid_point(requested_vpp=0.1)
    assert legacy.requested_source_vpp == 0.1
    assert legacy.requested_vpp == 0.1

    canonical = _valid_point(requested_source_vpp=0.2)
    assert canonical.requested_source_vpp == 0.2
    assert canonical.requested_vpp == 0.2

    updated = replace(legacy, requested_source_vpp=0.3)
    assert updated.requested_source_vpp == 0.3
    assert updated.requested_vpp == 0.3


def test_csv_contains_point_identity_and_signal_level_evidence(tmp_path: Path) -> None:
    point = _valid_point(
        case_id="case-0123",
        acquisition_id="acq-0456",
        capture_sync_grade=CAPTURE_SYNC_GRADE,
        requested_source_vpp=0.1,
        reference_plane=REFERENCE_PLANE,
        signal_level_evidence={
            "schema": "wavebench.frequency_response_evidence.v1",
            "requested_source_vpp": 0.1,
            "measured_reference_vpp": 1.0,
            "measured_response_vpp": 2.0,
        },
        plan_hash="plan-7890",
    )
    output = write_frequency_response_csv(tmp_path / "frequency_response.csv", [point])

    with output.open(newline="", encoding="utf-8") as file:
        row = next(csv.DictReader(file))
    fields = list(row)
    assert fields[:2] == ["index", "case_id"]
    assert row["case_id"] == "case-0123"
    assert row["acquisition_id"] == "acq-0456"
    assert row["requested_source_vpp"] == "0.1"
    assert row["requested_vpp"] == "0.1"
    assert row["reference_plane"] == "scope_input"
    assert row["plan_hash"] == "plan-7890"
    assert json.loads(row["signal_level_evidence"])["measured_reference_vpp"] == 1.0


def test_failed_point_keeps_failure_and_fit_exclusion_reasons() -> None:
    point = failed_frequency_response_point(
        index=4,
        requested_vpp=0.1,
        requested_frequency_hz=2_000.0,
        error="capture timed out",
        case_id="case-timeout",
        acquisition_id="acq-partial",
    )

    assert point.failure_reason == "capture timed out"
    assert point.exclusion_reason == "capture timed out"
    assert point.case_id == "case-timeout"
    assert point.acquisition_id == "acq-partial"


def test_failed_point_reason_is_exported_to_fit_document() -> None:
    failed = failed_frequency_response_point(
        index=4,
        requested_vpp=0.1,
        requested_frequency_hz=2_000.0,
        error="capture timed out",
        case_id="case-timeout",
        acquisition_id="acq-partial",
    )
    valid = _valid_point(index=0, requested_vpp=0.1)

    document, _values = build_fit_document([valid, failed], {"methods": ["linear_log"]})

    assert document is not None
    assert document["excluded_points"] == [
        {
            "index": 4,
            "case_id": "case-timeout",
            "frequency_hz": 2_000.0,
            "requested_vpp": 0.1,
            "reason": "capture timed out",
            "failure_reason": "capture timed out",
            "exclusion_reason": "capture timed out",
        }
    ]


def test_point_records_non_destructive_waveform_quality_metrics() -> None:
    times = np.arange(1000, dtype=float) / 100_000.0

    def waveform(amplitude: float):
        values = amplitude * np.sin(2 * np.pi * 1000.0 * times)
        return SimpleNamespace(
            channel=1,
            times_s=times,
            voltages_v=values,
            summary=lambda **_: {
                "quality_warnings": [],
                "frequency_estimate_hz": 1000.0,
                "voltage_vpp_v": amplitude * 2,
            },
        )

    point = analyze_frequency_response_point(
        index=0,
        requested_frequency_hz=1000.0,
        reference_waveform=waveform(1.0),
        response_waveform=waveform(2.0),
        frequency_tolerance_ratio=0.05,
        capture_package="capture",
        metadata_path="metadata.json",
    )

    assert point.quality_metrics["reference"]["sample_count"] == 1000
    assert point.quality_metrics["response"]["rms_v"] > 0


def test_capture_metadata_gets_stable_case_and_acquisition_evidence(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"operation": {"triggered_single": True}}), encoding="utf-8")
    case = case_id(
        plan_name="demo",
        step_index=0,
        label="dut",
        frequency_hz=1000.0,
        requested_vpp=0.1,
        reference_channel=1,
        response_channel=2,
    )
    acquisition = acquisition_id(tmp_path, label=case)
    evidence = annotate_capture_metadata(
        metadata,
        case=case,
        acquisition=acquisition,
        requested_frequency_hz=1000.0,
        requested_source_vpp=0.1,
        reference_channel=1,
        response_channel=2,
        reference_vpp_v=0.2,
        response_vpp_v=0.4,
        plan_hash="plan-hash",
    )
    saved = json.loads(metadata.read_text(encoding="utf-8"))
    assert evidence["case_id"] == case
    assert saved["evidence"]["acquisition_id"] == acquisition
    assert saved["evidence"]["signal_level"]["measured_reference_vpp"] == 0.2
