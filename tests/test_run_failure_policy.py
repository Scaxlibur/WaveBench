from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from wavebench.errors import ConfigError
from wavebench.logging import CommandLogger
from wavebench.services.run_analysis import step_status
from wavebench.services.run_artifacts import RunStepRecord
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunService


def _write_plan(tmp: str, content: str) -> Path:
    path = Path(tmp) / "plan.toml"
    path.write_text(content, encoding="utf-8")
    return path


def _service(tmp: str) -> RunService:
    # The loop tests use sleep-only plans, so no instrument configuration is needed.
    from test_run_service import make_config

    return RunService(config=make_config(tmp), logger=CommandLogger())


def _record(step_index: int, kind: str, status: str) -> RunStepRecord:
    return RunStepRecord(
        index=step_index,
        kind=kind,
        status=status,
        fields={},
        artifact={"test_status": status},
    )


def test_failed_step_stops_by_default_and_writes_stop_reason() -> None:
    with TemporaryDirectory() as tmp:
        plan = load_run_plan(
            _write_plan(
                tmp,
                """
[[steps]]
kind = "sleep"
duration_s = 0.01

[[steps]]
kind = "sleep"
duration_s = 0.01
""",
            )
        )
        service = _service(tmp)
        calls: list[int] = []

        def run_step(_plan, step, **_kwargs):
            calls.append(step.index)
            return _record(step.index, step.kind, "failed" if step.index == 0 else "ok")

        with patch.object(service, "_run_step", side_effect=run_step):
            result = service.run(plan)

        assert calls == [0]
        assert [record.status for record in result.steps] == ["failed"]
        run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
        assert run_data["status"] == "failed"
        assert run_data["error"] == {
            "type": "StepFailure",
            "code": "step_failed",
            "message": "run step 0 (sleep) failed",
            "step_index": 0,
            "step_kind": "sleep",
            "policy": "stop",
        }


def test_failed_step_can_explicitly_continue() -> None:
    with TemporaryDirectory() as tmp:
        plan = load_run_plan(
            _write_plan(
                tmp,
                """
[[steps]]
kind = "sleep"
duration_s = 0.01
on_failure = "continue"

[[steps]]
kind = "sleep"
duration_s = 0.01
""",
            )
        )
        assert plan.steps[0].fields["on_failure"] == "continue"
        service = _service(tmp)
        calls: list[int] = []

        def run_step(_plan, step, **_kwargs):
            calls.append(step.index)
            return _record(step.index, step.kind, "failed" if step.index == 0 else "ok")

        with patch.object(service, "_run_step", side_effect=run_step):
            result = service.run(plan)

        assert calls == [0, 1]
        assert [record.status for record in result.steps] == ["failed", "ok"]
        run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
        assert run_data["status"] == "failed"
        assert "error" not in run_data


def test_on_failure_rejects_unknown_policy() -> None:
    with TemporaryDirectory() as tmp:
        plan_path = _write_plan(
            tmp,
            """
[[steps]]
kind = "sleep"
duration_s = 0.01
on_failure = "ask"
""",
        )
        with pytest.raises(ConfigError, match="on_failure must be 'stop' or 'continue'"):
            load_run_plan(plan_path)


def test_quality_gate_warning_is_a_failed_step() -> None:
    assert step_status({"quality_gate": {"status": "warning"}}) == "failed"
    assert step_status({"quality": {"status": "warning"}}) == "ok"
