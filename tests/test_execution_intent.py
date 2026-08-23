from __future__ import annotations

import json
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from wavebench.errors import ExecutionIntentError, error_envelope
from wavebench.cli import main
from wavebench.logging import CommandLogger
from wavebench.services.execution_intent import (
    INTENT_SCHEMA,
    build_execution_intent,
    verify_execution_intent,
)
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunService

from test_run_service import make_config, write_plan


def _sleep_plan(tmp: str, duration_s: float = 0.01):
    return load_run_plan(
        write_plan(
            tmp,
            f"""
[[steps]]
kind = "sleep"
duration_s = {duration_s}
""",
        )
    )


def test_execution_intent_is_stable_and_does_not_expose_resources() -> None:
    with TemporaryDirectory() as tmp:
        plan = _sleep_plan(tmp)
        config = make_config(tmp)

        first = build_execution_intent(plan, config)
        second = build_execution_intent(plan, config)

        assert first == second
        assert first.schema == INTENT_SCHEMA
        assert first.operations[0]["operation"] == "run.sleep"
        assert first.operations[0]["session_purpose"] == "normal"
        assert first.operations[0]["required_verified_fields"] == []
        assert first.operations[0]["verification_fields"] == []
        assert first.operations[0]["timeout_source"] == "connection.timeout_ms"
        assert "TCPIP::" not in json.dumps(first.as_dict())


def test_execution_intent_rejects_plan_or_config_change() -> None:
    with TemporaryDirectory() as tmp:
        plan = _sleep_plan(tmp)
        config = make_config(tmp)
        expected = build_execution_intent(plan, config).as_dict()

        changed_plan = _sleep_plan(tmp, duration_s=0.02)
        with pytest.raises(ExecutionIntentError) as plan_error:
            verify_execution_intent(expected, changed_plan, config)
        assert plan_error.value.expected_plan_digest != plan_error.value.actual_plan_digest

        changed_config = config.with_connection_timeout_ms(2000)
        with pytest.raises(ExecutionIntentError) as config_error:
            verify_execution_intent(expected, plan, changed_config)
        assert config_error.value.expected_config_digest != config_error.value.actual_config_digest


def test_execution_intent_rejects_payload_change() -> None:
    with TemporaryDirectory() as tmp:
        payload = Path(tmp) / "waveform.npy"
        payload.write_bytes(b"first")
        plan = load_run_plan(
            write_plan(
                tmp,
                """
[[steps]]
kind = "source.arb_load"
channel = 1
file = "waveform.npy"
frequency_hz = 1000
amplitude_vpp = 1
""",
            )
        )
        config = make_config(tmp)
        expected = build_execution_intent(plan, config).as_dict()

        payload.write_bytes(b"second")
        with pytest.raises(ExecutionIntentError):
            verify_execution_intent(expected, plan, config)


def test_execution_intent_uses_distinct_source_v2_output_operations() -> None:
    with TemporaryDirectory() as tmp:
        plan = load_run_plan(
            write_plan(
                tmp,
                """
[[steps]]
kind = "source.basic_configure_v2"
channel = 1
frequency_hz = 2000

[[steps]]
kind = "source.output_enable_v2"
channel = 1

[[steps]]
kind = "source.output_disable_v2"
channel = 1

[[steps]]
kind = "source.harmonics_configure_v2"
channel = 1
order = 8
preset = "odd"

[[steps]]
kind = "source.modulation_configure_v2"
channel = 1
depth_percent = 80
internal_frequency_hz = 25

[[steps]]
kind = "source.modulation_pm_configure_v2"
channel = 1
phase_deviation_deg = 90
internal_frequency_hz = 25

[[steps]]
kind = "source.modulation_fm_configure_v2"
channel = 1
frequency_deviation_hz = 12500
internal_frequency_hz = 25

[[steps]]
kind = "source.modulation_pwm_configure_v2"
channel = 1
internal_frequency_hz = 25
width_deviation_s = 1e-6

[[steps]]
kind = "source.sweep_configure_v2"
channel = 1
start_hz = 100
stop_hz = 1000
spacing = "linear"
steps = 101
sweep_time_s = 1

[[steps]]
kind = "source.burst_configure_v2"
channel = 1
cycles = 12
phase_deg = 30
internal_period_s = 0.25
delay_s = 0.5

[[steps]]
kind = "source.pulse_configure_v2"
channel = 1
width_s = 1e-6
delay_s = 0
leading_transition_s = 1e-8
trailing_transition_s = 1e-8
""",
            )
        )

        intent = build_execution_intent(plan, make_config(tmp))

    assert [item["operation"] for item in intent.operations] == [
        "source.basic_configure_v2",
        "source.output_enable_v2",
        "source.output_disable_v2",
        "source.harmonics_configure_v2",
        "source.modulation_configure_v2",
        "source.modulation_pm_configure_v2",
        "source.modulation_fm_configure_v2",
        "source.modulation_pwm_configure_v2",
        "source.sweep_configure_v2",
        "source.burst_configure_v2",
        "source.pulse_configure_v2",
    ]
    assert intent.operations[0]["parameters"]["frequency_hz"] == 2000.0
    assert intent.operations[3]["parameters"] == {"channel": 1, "order": 8, "preset": "odd"}
    assert intent.operations[3]["risk_flags"] == ["source_v2", "output_must_be_off"]
    assert intent.operations[4]["parameters"] == {
        "channel": 1,
        "depth_percent": 80.0,
        "internal_frequency_hz": 25.0,
    }
    assert intent.operations[4]["risk_flags"] == [
        "source_v2",
        "output_must_be_off",
        "am_internal_only",
    ]
    assert intent.operations[5]["parameters"] == {
        "channel": 1,
        "phase_deviation_deg": 90.0,
        "internal_frequency_hz": 25.0,
    }
    assert intent.operations[5]["risk_flags"] == [
        "source_v2",
        "output_must_be_off",
        "pm_internal_only",
    ]
    assert intent.operations[6]["parameters"] == {
        "channel": 1,
        "frequency_deviation_hz": 12_500.0,
        "internal_frequency_hz": 25.0,
    }
    assert intent.operations[6]["risk_flags"] == [
        "source_v2",
        "output_must_be_off",
        "fm_internal_only",
    ]
    assert intent.operations[7]["parameters"] == {
        "channel": 1,
        "internal_frequency_hz": 25.0,
        "width_deviation_s": 1.0e-6,
    }
    assert intent.operations[7]["risk_flags"] == [
        "source_v2",
        "output_must_be_off",
        "pwm_internal_only",
    ]
    assert intent.operations[8]["parameters"] == {
        "channel": 1,
        "start_hz": 100.0,
        "stop_hz": 1_000.0,
        "spacing": "linear",
        "steps": 101,
        "sweep_time_s": 1.0,
    }
    assert intent.operations[8]["risk_flags"] == [
        "source_v2",
        "output_must_be_off",
        "sweep_internal_no_fire",
    ]
    assert intent.operations[9]["parameters"] == {
        "channel": 1,
        "cycles": 12,
        "phase_deg": 30.0,
        "internal_period_s": 0.25,
        "delay_s": 0.5,
    }
    assert intent.operations[9]["risk_flags"] == [
        "source_v2",
        "output_must_be_off",
        "burst_internal_triggered_only",
    ]
    assert intent.operations[10]["parameters"] == {
        "channel": 1,
        "width_s": 1.0e-6,
        "delay_s": 0.0,
        "leading_transition_s": 1.0e-8,
        "trailing_transition_s": 1.0e-8,
    }
    assert intent.operations[10]["risk_flags"] == [
        "source_v2",
        "output_must_be_off",
        "pulse_width_only",
    ]


def test_execution_intent_uses_distinct_source_v2_cross_channel_operations() -> None:
    with TemporaryDirectory() as tmp:
        plan = load_run_plan(
            write_plan(
                tmp,
                """
[[steps]]
kind = "source.combine_configure_v2"
channels = [1, 2]
enabled = true

[[steps]]
kind = "source.coupling_configure_v2"
channels = [1, 2]
enabled = false

[[steps]]
kind = "source.tracking_configure_v2"
channels = [1, 2]
enabled = true

[[steps]]
kind = "source.phase_relation_configure_v2"
channels = [1, 2]
enabled = false
""",
            )
        )

        intent = build_execution_intent(plan, make_config(tmp))

    assert [item["operation"] for item in intent.operations] == [
        "source.combine_configure_v2",
        "source.coupling_configure_v2",
        "source.tracking_configure_v2",
        "source.phase_relation_configure_v2",
    ]
    assert [item["parameters"] for item in intent.operations] == [
        {"channels": [1, 2], "enabled": True},
        {"channels": [1, 2], "enabled": False},
        {"channels": [1, 2], "enabled": True},
        {"channels": [1, 2], "enabled": False},
    ]
    assert all(
        item["risk_flags"]
        == ["source_v2", "output_must_be_off", "cross_channel_relation"]
        for item in intent.operations
    )


def test_run_rejects_intent_mismatch_before_opening_instrument_services() -> None:
    with TemporaryDirectory() as tmp:
        plan = _sleep_plan(tmp)
        config = make_config(tmp)
        expected = build_execution_intent(plan, config).as_dict()
        expected["intent_digest"] = "stale"
        service = RunService(config=config, logger=CommandLogger())

        with patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ExecutionIntentError):
                service.run(plan, execution_intent=expected)
        open_services.assert_not_called()


def test_execution_intent_error_has_stable_envelope_details() -> None:
    error = ExecutionIntentError(
        "intent mismatch",
        expected_digest="old",
        actual_digest="new",
    )
    envelope = error_envelope(error)
    assert envelope["code"] == "execution_intent_mismatch"
    assert envelope["details"]["expected_digest"] == "old"
    assert envelope["details"]["actual_digest"] == "new"


def test_run_provenance_records_execution_intent() -> None:
    with TemporaryDirectory() as tmp:
        plan = _sleep_plan(tmp)
        result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)
        run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))

        intent = run_data["provenance"]["execution_intent"]
        assert intent["schema"] == INTENT_SCHEMA
        assert intent["intent_digest"]


def test_run_intent_cli_emits_offline_json_without_opening_instruments() -> None:
    with TemporaryDirectory() as tmp:
        plan = _sleep_plan(tmp)
        config = make_config(tmp)
        service = type(
            "OfflineRunService",
            (),
            {"config": config, "check": lambda self, value: None},
        )()
        stdout = io.StringIO()
        with patch("wavebench.cli._load_run_service", return_value=service), redirect_stdout(stdout):
            code = main(["run", "intent", "--plan", str(plan.path), "--json"])

        assert code == 0
        payload = json.loads(stdout.getvalue())
        assert payload["schema"] == "wavebench.cli.result.v1"
        assert payload["result"]["schema"] == INTENT_SCHEMA
        assert payload["result"]["intent_digest"]


def test_json_wrapper_turns_argparse_errors_into_one_error_envelope() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("sys.stderr", stderr), redirect_stdout(stdout):
        code = main(["--json", "capability", "explain"])

    assert code == 2
    payload = json.loads(stdout.getvalue())
    assert payload["schema"] == "wavebench.error.v1"
    assert payload["code"] == "config_error"
    assert payload["exit_code"] == 2
    assert "usage:" in stderr.getvalue()
