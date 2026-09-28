from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from wavebench.logging import CommandLogger
from wavebench.services.frequency_response import FrequencyResponsePoint
from wavebench.services.frequency_response_evidence import case_id, plan_digest
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunService

from test_run_service import fake_frequency_response_capture, make_config, write_plan


def test_run_service_reuses_matching_resume_points_and_captures_only_pending(tmp_path: Path) -> None:
    with TemporaryDirectory(dir=tmp_path) as temporary:
        root = Path(temporary)
        old_csv = root / "old.csv"
        plan_path = write_plan(
            temporary,
            """
[experiment]
name = "resume-service"
label = "resume-service"

[[steps]]
kind = "sweep.frequency_response"
label = "dut"
source_channel = 1
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
settle_s = 0
resume_from = "old.csv"
""",
        )
        plan = load_run_plan(plan_path)
        step = plan.steps[0]
        point_case = case_id(
            plan_name=plan.name,
            step_index=step.index,
            label="dut",
            frequency_hz=100,
            requested_vpp=None,
            reference_channel=1,
            response_channel=2,
        )
        old_point = FrequencyResponsePoint(
            index=0,
            requested_frequency_hz=100,
            reference_frequency_hz=100,
            response_frequency_hz=100,
            reference_amplitude_peak_v=1,
            response_amplitude_peak_v=2,
            reference_vpp_v=2,
            response_vpp_v=4,
            gain_linear=2,
            gain_db=6.0206,
            phase_wrapped_deg=0,
            phase_unwrapped_deg=0,
            status="ok",
            case_id=point_case,
            plan_hash=plan_digest(plan),
        )
        with old_csv.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(old_point.as_csv_row()))
            writer.writeheader()
            writer.writerow(old_point.as_csv_row())

        capture = fake_frequency_response_capture(temporary, "pending", frequency_hz=1000)
        status = SimpleNamespace(output="ON")
        with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
            "wavebench.services.run_service.SourceService"
        ) as source_cls:
            source = source_cls.return_value
            source.status.return_value = status
            source.set_frequency.return_value = status
            scope = scope_cls.return_value
            scope.capture_waveforms.return_value = capture

            result = RunService(config=make_config(temporary), logger=CommandLogger()).run(plan)

        assert scope.capture_waveforms.call_count == 1
        response = result.steps[0].artifact["frequency_response"]
        assert response["resume"]["reused_point_count"] == 1
        assert response["point_count"] == 2
