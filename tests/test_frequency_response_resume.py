from __future__ import annotations

import json
from pathlib import Path

from wavebench.services.frequency_response_resume import (
    RESUME_SCHEMA,
    build_frequency_response_resume,
)
from wavebench.services.frequency_response_evidence import plan_digest
from wavebench.services.run_plan import load_run_plan


def _plan(tmp_path: Path) -> Path:
    path = tmp_path / "plan.toml"
    path.write_text(
        """
[experiment]
name = "resume-demo"
label = "resume-demo"

[[steps]]
kind = "sweep.frequency_response"
label = "dut"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
amplitudes_vpp = [0.1]
settle_s = 0
""",
        encoding="utf-8",
    )
    return path


def test_resume_manifest_separates_reusable_and_pending_points(tmp_path: Path) -> None:
    plan_path = _plan(tmp_path)
    csv_path = tmp_path / "frequency_response.csv"
    csv_path.write_text(
        "index,case_id,requested_frequency_hz,requested_vpp,gain_linear,gain_db,status\n"
        "0,,100,0.1,2,6,ok\n"
        "1,,1000,0.1,,,failed\n",
        encoding="utf-8",
    )

    manifest = build_frequency_response_resume(csv_path, load_run_plan(plan_path))

    assert manifest.as_dict()["schema_version"] == RESUME_SCHEMA
    assert len(manifest.reusable_cases) == 1
    assert len(manifest.pending_cases) == 1
    assert manifest.pending_cases[0]["requested_frequency_hz"] == 1000.0
    assert len(manifest.rejected_points) == 1

    output = manifest.write(tmp_path / "resume.json")
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["pending"] == 1


def test_resume_selector_does_not_change_measurement_plan_hash(tmp_path: Path) -> None:
    original = load_run_plan(_plan(tmp_path))
    resumed_path = tmp_path / "resumed.toml"
    resumed_path.write_text(
        (_plan(tmp_path).read_text(encoding="utf-8")).replace(
            "amplitudes_vpp = [0.1]", "amplitudes_vpp = [0.1]\nresume_from = '../data/runs/old/frequency_response.csv'"
        ),
        encoding="utf-8",
    )

    assert plan_digest(original) == plan_digest(load_run_plan(resumed_path))
