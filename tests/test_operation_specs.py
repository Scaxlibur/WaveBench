from __future__ import annotations

import pytest

from wavebench.errors import ConfigError
from wavebench.services.operation_specs import (
    OPERATION_REGISTRY,
    OperationRegistry,
    OperationSpec,
    get_operation_spec,
    list_operation_specs,
    require_operation_spec,
)


def test_source_output_spec_describes_mutation_and_restore_boundary() -> None:
    spec = require_operation_spec("source.output")

    assert spec.instrument_kind == "source"
    assert spec.effect == "write"
    assert spec.mutates is True
    assert spec.lease_mode == "exclusive"
    assert spec.changed_fields == ("output",)
    assert spec.restore_coverage == "basic"
    assert "dangerous_output" in spec.risk_flags
    assert spec.as_dict()["required_capabilities"] == ["source.output"]


def test_registry_is_read_only_and_filters_by_instrument_kind() -> None:
    assert get_operation_spec("run.check") is not None
    assert get_operation_spec("does.not.exist") is None
    assert all(spec.instrument_kind == "power" for spec in list_operation_specs(instrument_kind="power"))
    assert OPERATION_REGISTRY.get("run.schema").effect == "offline"


def test_scope_capture_specs_freeze_side_effect_and_verification_closures() -> None:
    transfer_state_fields = {
        "scope.query_response_header",
        "scope.waveform_format",
        "scope.waveform_byte_order",
        "scope.waveform_points",
        "scope.waveform_transfer_window",
    }
    for operation in (
        "scope.capture",
        "scope.capture_waveforms",
        "scope.capture_multiple",
        "scope.fetch_waveform",
    ):
        spec = require_operation_spec(operation)
        assert spec.session_purpose == "normal"
        assert spec.timeout_source == "connection.timeout_ms"
        assert "scope.run_state" in spec.changed_fields
        assert transfer_state_fields <= set(spec.changed_fields)
        assert "scope.capture_identity" in spec.changed_fields
        assert "output.waveform_package" in spec.changed_fields
        assert spec.required_verified_fields == ("scope.identity",)
        assert set(spec.required_verified_fields) <= set(spec.verification_fields)
        assert transfer_state_fields <= set(spec.verification_fields)
        assert set(spec.verification_fields) - set(spec.required_verified_fields) <= set(
            spec.changed_fields
        )
        assert "scope.capture_identity" in spec.verification_fields
        assert spec.restore_coverage == "capture-baseline-only"


def test_unknown_operation_has_config_error_for_cli_compatible_exit_code() -> None:
    with pytest.raises(ConfigError, match="unknown WaveBench operation") as raised:
        require_operation_spec("missing.operation")
    assert raised.value.exit_code == 2


def test_operation_spec_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="unsupported operation effect"):
        OperationSpec("bad", "scope", effect="mutate")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="session purpose"):
        OperationSpec("bad", "scope", session_purpose="unsafe")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="overlap"):
        OperationSpec(
            "bad",
            "scope",
            required_capabilities=("scope.idn",),
            optional_capabilities=("scope.idn",),
        )


def test_registry_rejects_key_spec_mismatch() -> None:
    spec = OperationSpec("actual", None, effect="offline", lease_mode="none")
    with pytest.raises(ValueError, match="keys must match"):
        OperationRegistry({"wrong": spec})
