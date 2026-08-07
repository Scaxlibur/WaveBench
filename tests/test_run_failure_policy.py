from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from wavebench.errors import ConfigError
from wavebench.logging import CommandLogger
from wavebench.services.run_analysis import step_status
from wavebench.services.run_artifacts import RunStepRecord
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunInstrumentServices, RunService


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


def test_safety_gate_turns_off_authorized_outputs_before_stopping() -> None:
    with TemporaryDirectory() as tmp:
        plan = load_run_plan(
            _write_plan(
                tmp,
                """
[safety]
safety_gate = true
off_source_channels = [1]
off_power_channels = [2]

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
        service = _service(tmp)
        source = patch("wavebench.services.run_service.SourceService").start()
        power = patch("wavebench.services.run_service.PowerService").start()
        try:
            source_instance = source.return_value
            source_instance.set_output.return_value = SimpleNamespace(output="OFF")
            power_instance = power.return_value
            power_instance.set_output.return_value = SimpleNamespace(output="OFF")
            services = RunInstrumentServices(source=source_instance, power=power_instance)
            calls: list[int] = []

            def run_step(_plan, step, **_kwargs):
                calls.append(step.index)
                return _record(step.index, step.kind, "failed" if step.index == 0 else "ok")

            with patch.object(service, "check"), patch.object(
                service, "_run_instrument_services", return_value=nullcontext(services)
            ), patch.object(service, "_run_step", side_effect=run_step):
                result = service.run(plan)

            assert calls == [0]
            source_instance.set_output.assert_called_once_with(channel=1, enabled=False)
            power_instance.set_output.assert_called_once_with(channel=2, enabled=False)
            run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
            assert run_data["error"]["code"] == "safety_gate_failed"
            assert run_data["steps"][0]["artifact"]["safety_gate"]["status"] == "ok"
        finally:
            patch.stopall()


def test_step_safety_gate_can_infer_source_channel_and_capture_gate_failure() -> None:
    with TemporaryDirectory() as tmp:
        plan = load_run_plan(
            _write_plan(
                tmp,
                """
[[steps]]
kind = "source.set_freq"
channel = 3
frequency_hz = 1000
safety_gate = true
""",
            )
        )
        service = _service(tmp)
        source = patch("wavebench.services.run_service.SourceService").start()
        try:
            source_instance = source.return_value
            source_instance.set_output.side_effect = ConfigError("OFF failed")
            services = RunInstrumentServices(source=source_instance)
            with patch.object(service, "check"), patch.object(
                service, "_run_instrument_services", return_value=nullcontext(services)
            ), patch.object(
                service,
                "_run_step",
                return_value=_record(0, "source.set_freq", "failed"),
            ):
                result = service.run(plan)

            source_instance.set_output.assert_called_once_with(channel=3, enabled=False)
            run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
            assert run_data["error"]["code"] == "safety_gate_failed"
            assert run_data["error"]["safety_gate"]["status"] == "failed"
            assert run_data["error"]["safety_gate"]["errors"][0]["message"] == "OFF failed"
        finally:
            patch.stopall()


def test_safety_gate_plan_fields_are_normalized() -> None:
    with TemporaryDirectory() as tmp:
        plan = load_run_plan(
            _write_plan(
                tmp,
                """
[safety]
safety_gate = true
off_source_channels = [1, 2]
off_power_channels = 3

[[steps]]
kind = "sleep"
duration_s = 0.01
safety_gate = { source_channels = [4], power_channels = [5] }
""",
            )
        )
        assert plan.safety.safety_gate is True
        assert plan.safety.off_source_channels == (1, 2)
        assert plan.safety.off_power_channels == (3,)
        assert plan.steps[0].fields["safety_gate"] == {
            "enabled": True,
            "source_channels": [4],
            "power_channels": [5],
        }


def test_safety_gate_remains_off_after_source_restore() -> None:
    with TemporaryDirectory() as tmp:
        plan = load_run_plan(
            _write_plan(
                tmp,
                """
[restore]
source_state = true
source_channel = 1

[[steps]]
kind = "source.set_freq"
channel = 1
frequency_hz = 1000
safety_gate = true
""",
            )
        )
        service = _service(tmp)
        source = patch("wavebench.services.run_service.SourceService").start()
        try:
            source_instance = source.return_value
            state = SimpleNamespace(channel=1, as_dict=lambda: {"channel": 1})
            source_instance.snapshot_restorable_state.return_value = state
            source_instance.restore_restorable_state.return_value = SimpleNamespace(output="ON")
            source_instance.set_output.return_value = SimpleNamespace(output="OFF")
            services = RunInstrumentServices(source=source_instance)
            with patch.object(service, "check"), patch.object(
                service, "_run_instrument_services", return_value=nullcontext(services)
            ), patch.object(
                service,
                "_run_step",
                return_value=_record(0, "source.set_freq", "failed"),
            ):
                result = service.run(plan)

            assert source_instance.restore_restorable_state.call_count == 1
            assert source_instance.set_output.call_count == 2
            run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
            assert run_data["error"]["safety_gate"]["post_restore"]["status"] == "ok"
        finally:
            patch.stopall()
