from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace

from wavebench.cli import main
from wavebench.services.capability_explain import explain_operation


def _descriptor(*capabilities: str, kind: str = "source") -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.driver",
        kind=kind,
        capabilities=capabilities,
    )


def test_explain_reports_supported_operation_without_opening_hardware() -> None:
    result = explain_operation(
        "source.output",
        descriptor=_descriptor("source.output"),
    )

    assert result.status == "supported"
    assert result.missing_capabilities == ()
    assert result.spec is not None
    assert result.spec.effect == "write"


def test_explain_reports_missing_capability_and_safe_alternative() -> None:
    result = explain_operation(
        "source.output",
        descriptor=_descriptor("source.status"),
    )

    assert result.status == "missing_capability"
    assert result.missing_capabilities == ("source.output",)
    assert result.spec is not None
    assert result.spec.safe_alternatives == ("source.status",)


def test_explain_reports_access_denied_for_read_only_write() -> None:
    result = explain_operation(
        "source.output",
        descriptor=_descriptor("source.output"),
        access="read_only",
    )

    assert result.status == "access_denied"
    assert result.access == "read_only"


def test_explain_reports_unknown_operation_without_driver() -> None:
    result = explain_operation("source.not_registered")
    assert result.status == "unknown_operation"


def test_capability_cli_json_is_one_machine_readable_result() -> None:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(
            [
                "capability",
                "explain",
                "source.output",
                "--driver",
                "dg4202",
                "--access",
                "read_only",
                "--json",
            ]
        )

    assert code == 2
    payload = json.loads(stdout.getvalue())
    assert payload["schema"] == "wavebench.cli.result.v1"
    assert payload["status"] == "access_denied"
    assert payload["result"]["status"] == "access_denied"
    assert payload["result"]["spec"]["operation"] == "source.output"


def test_capability_cli_can_explain_partial_scope_status_offline() -> None:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(
            ["capability", "explain", "scope.status", "--driver", "rtm2032"]
        )

    assert code == 0
    assert "status=supported\n" in stdout.getvalue()
    assert "missing_optional_capabilities=scope.snapshot\n" in stdout.getvalue()


def test_scope_extension_explain_includes_timeout_binary_and_recovery_contract() -> None:
    result = explain_operation(
        "scope.fetch_trace",
        descriptor=_descriptor("scope.fetch_trace", kind="scope"),
    )

    assert result.status == "supported"
    assert result.spec is not None
    assert result.spec.operation_timeout_ms == 60_000
    assert result.spec.binary_response_max_bytes == 8_388_608
    assert "scope.waveform_byte_order" in result.spec.verification_fields
    assert "scope.waveform_transfer_window" in result.spec.cleanup_verification_fields


def test_capability_cli_lists_local_candidates_without_installing() -> None:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(
            [
                "capability",
                "explain",
                "source.output",
                "--candidates",
                "--json",
            ]
        )

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["result"]["supported_count"] >= 1
    assert any(
        item["driver_id"] == "rigol.dg4202"
        for item in payload["result"]["candidates"]
    )
