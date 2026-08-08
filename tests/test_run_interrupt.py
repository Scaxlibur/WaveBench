from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from wavebench.errors import ConfigError
from wavebench.logging import CommandLogger
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunService

from test_run_service import make_config, write_plan


def _interrupt_plan(tmp: str) -> object:
    return load_run_plan(
        write_plan(
            tmp,
            """
[restore]
source_state = true
source_channel = 2

[[steps]]
kind = "source.set_func"
channel = 2
function = "SQU"
""",
        )
    )


def test_keyboard_interrupt_restores_source_and_writes_failed_artifact() -> None:
    with TemporaryDirectory() as tmp:
        plan = _interrupt_plan(tmp)
        fake_state = type("State", (), {"channel": 2, "as_dict": lambda self: {"channel": 2}})()
        with patch("wavebench.services.run_service.SourceService") as source_cls:
            source = source_cls.return_value
            source.snapshot_restorable_state.return_value = fake_state
            source.set_function.side_effect = KeyboardInterrupt()

            with pytest.raises(KeyboardInterrupt):
                RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            source.restore_restorable_state.assert_called_once_with(fake_state)
        run_dir = next((Path(tmp) / "data" / "runs").iterdir())
        run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert run_data["status"] == "failed"
        assert run_data["error"]["type"] == "KeyboardInterrupt"
        assert run_data["restore"]["status"] == "ok"


def test_restore_failure_turns_interrupt_into_top_level_config_error_with_cause() -> None:
    with TemporaryDirectory() as tmp:
        plan = _interrupt_plan(tmp)
        fake_state = type("State", (), {"channel": 2, "as_dict": lambda self: {"channel": 2}})()
        with patch("wavebench.services.run_service.SourceService") as source_cls:
            source = source_cls.return_value
            source.snapshot_restorable_state.return_value = fake_state
            source.set_function.side_effect = KeyboardInterrupt()
            source.restore_restorable_state.side_effect = ConfigError("restore boom")

            with pytest.raises(ConfigError, match="restore boom") as raised:
                RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            assert isinstance(raised.value.__cause__, KeyboardInterrupt)
        run_dir = next((Path(tmp) / "data" / "runs").iterdir())
        run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert run_data["error"]["type"] == "RestoreError"
        assert run_data["error"]["cause"]["type"] == "KeyboardInterrupt"
        assert run_data["restore"]["status"] == "failed"
