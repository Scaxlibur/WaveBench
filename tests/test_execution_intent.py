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
        assert payload["schema"] == INTENT_SCHEMA
        assert payload["intent_digest"]
