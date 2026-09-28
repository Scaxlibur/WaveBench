from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from wavebench.cli import build_parser, main
from wavebench.instruments.rf_source_extensions import (
    RfPulseOutputRequest,
    RfPulseOutputResult,
)


def test_rf_source_parser_accepts_a_bounded_physical_pulse_output_command() -> None:
    arguments = build_parser().parse_args(
        [
            "rf-source",
            "pulse-output",
            "--port",
            "rf_out",
            "--interface",
            "pulse_in_out",
            "on",
        ]
    )

    assert (arguments.domain, arguments.command) == ("rf-source", "pulse-output")
    assert arguments.port == "rf_out"
    assert arguments.interface_id == "pulse_in_out"
    assert arguments.state == "on"


def test_rf_source_cli_dispatches_a_typed_physical_pulse_output_request() -> None:
    service = Mock()
    service.set_pulse_output.return_value = RfPulseOutputResult(
        port_id="rf_out",
        interface_id="pulse_in_out",
        enabled=True,
        write_completed=True,
    )

    stdout = io.StringIO()
    with patch("wavebench.cli._load_rf_source_service", return_value=service), redirect_stdout(stdout):
        assert (
            main(
                [
                    "--json",
                    "rf-source",
                    "pulse-output",
                    "--port",
                    "rf_out",
                    "--interface",
                    "pulse_in_out",
                    "on",
                ]
            )
            == 0
        )

    payload = json.loads(stdout.getvalue())
    assert payload["result"] == {
        "port_id": "rf_out",
        "interface_id": "pulse_in_out",
        "enabled": True,
        "write_completed": True,
    }
    service.set_pulse_output.assert_called_once_with(
        RfPulseOutputRequest(
            port_id="rf_out",
            interface_id="pulse_in_out",
            enabled=True,
        )
    )
