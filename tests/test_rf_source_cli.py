from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wavebench.cli import _load_rf_source_service, build_parser, main
from wavebench.instruments.rf_source_extensions import RfCwRequest, RfCwResult


def test_rf_source_parser_accepts_read_only_and_off_only_cw_commands() -> None:
    identity = build_parser().parse_args(
        ["rf-source", "idn", "--config", "rf.toml", "--resource", "TCPIP::rf::INSTR"]
    )
    status = build_parser().parse_args(["rf-source", "status"])
    frequency = build_parser().parse_args(
        ["rf-source", "set-frequency", "--port", "rf_out", "4000000"]
    )
    power = build_parser().parse_args(
        ["rf-source", "set-power", "--port", "rf_out", "-20"]
    )

    assert (identity.domain, identity.command) == ("rf-source", "idn")
    assert identity.config == "rf.toml"
    assert identity.resource == "TCPIP::rf::INSTR"
    assert (status.domain, status.command) == ("rf-source", "status")
    assert (frequency.domain, frequency.command) == ("rf-source", "set-frequency")
    assert frequency.port == "rf_out"
    assert frequency.frequency_hz == 4_000_000.0
    assert (power.domain, power.command) == ("rf-source", "set-power")
    assert power.power_dbm == -20.0


def test_rf_source_cli_dispatches_identity_and_typed_snapshot() -> None:
    service = Mock()
    service.idn.return_value = "EXAMPLE,RF1,0,1"
    service.snapshot.return_value = SimpleNamespace(
        as_dict=lambda: {
            "schema": "wavebench.rf_source.snapshot.v1",
            "ports": [],
            "protection": {"availability": "unknown"},
        }
    )

    identity_stdout = io.StringIO()
    with patch("wavebench.cli._load_rf_source_service", return_value=service), redirect_stdout(
        identity_stdout
    ):
        assert main(["rf-source", "idn"]) == 0
    assert identity_stdout.getvalue().strip() == "EXAMPLE,RF1,0,1"
    service.idn.assert_called_once_with()

    status_stdout = io.StringIO()
    with patch("wavebench.cli._load_rf_source_service", return_value=service), redirect_stdout(
        status_stdout
    ):
        assert main(["--json", "rf-source", "status"]) == 0
    payload = json.loads(status_stdout.getvalue())
    assert payload["schema"] == "wavebench.cli.result.v1"
    assert payload["result"]["schema"] == "wavebench.rf_source.snapshot.v1"
    service.snapshot.assert_called_once_with()


def test_rf_source_cli_dispatches_each_off_only_cw_request() -> None:
    service = Mock()
    service.configure_cw.side_effect = [
        RfCwResult(port_id="rf_out", frequency_hz=4_000_000.0),
        RfCwResult(port_id="rf_out", power_dbm=-20.0),
    ]

    frequency_stdout = io.StringIO()
    with patch("wavebench.cli._load_rf_source_service", return_value=service), redirect_stdout(
        frequency_stdout
    ):
        assert main(["--json", "rf-source", "set-frequency", "--port", "rf_out", "4000000"]) == 0
    frequency_payload = json.loads(frequency_stdout.getvalue())
    assert frequency_payload["result"]["frequency_hz"] == 4_000_000.0

    power_stdout = io.StringIO()
    with patch("wavebench.cli._load_rf_source_service", return_value=service), redirect_stdout(
        power_stdout
    ):
        assert main(["--json", "rf-source", "set-power", "--port", "rf_out", "-20"]) == 0
    power_payload = json.loads(power_stdout.getvalue())
    assert power_payload["result"]["power_dbm"] == -20.0
    assert service.configure_cw.call_args_list == [
        ((RfCwRequest(port_id="rf_out", frequency_hz=4_000_000.0),), {}),
        ((RfCwRequest(port_id="rf_out", power_dbm=-20.0),), {}),
    ]


def test_rf_source_resource_override_does_not_touch_source_config() -> None:
    updated = object()
    config = SimpleNamespace(with_rf_source_resource=Mock(return_value=updated))
    args = SimpleNamespace(config="rf.toml", resource="TCPIP::rf::INSTR")

    with patch("wavebench.cli.load_config", return_value=config), patch(
        "wavebench.cli.RfSourceService"
    ) as service_type:
        result = _load_rf_source_service(args)

    config.with_rf_source_resource.assert_called_once_with("TCPIP::rf::INSTR")
    service_type.assert_called_once()
    assert service_type.call_args.kwargs["config"] is updated
    assert result is service_type.return_value
