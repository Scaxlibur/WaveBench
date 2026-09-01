from __future__ import annotations

import pytest

from wavebench.errors import ConfigError
from wavebench.instruments.source_extensions import (
    SOURCE_ARBITRARY_SELECT_V2_OPERATION_CONTRACT,
    SOURCE_ARBITRARY_STORAGE_V2_OPERATION_CONTRACT,
    SOURCE_ARBITRARY_VOLATILE_REPLACE_V2_OPERATION_CONTRACT,
    SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_BURST_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_BURST_FIRE_V2_OPERATION_CONTRACT,
    SOURCE_COUNTER_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_COUNTER_DISABLE_V2_OPERATION_CONTRACT,
    SOURCE_COUNTER_ENABLE_V2_OPERATION_CONTRACT,
    SOURCE_FM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_HARMONICS_DISABLE_V2_OPERATION_CONTRACT,
    SOURCE_HARMONICS_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_OUTPUT_DISABLE_V2_OPERATION_CONTRACT,
    SOURCE_OUTPUT_ENABLE_V2_OPERATION_CONTRACT,
    SOURCE_PM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_PULSE_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_PWM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_SWEEP_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_SWEEP_FIRE_V2_OPERATION_CONTRACT,
    SourceEnergyEffect,
)
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


def test_source_counter_v2_specs_keep_configuration_enable_and_measure_separate() -> None:
    configure = require_operation_spec("source.counter_configure_v2")
    enable = require_operation_spec("source.counter_enable_v2")
    disable = require_operation_spec("source.counter_disable_v2")
    measure = require_operation_spec("source.counter_measure_v2")

    for spec, contract in (
        (configure, SOURCE_COUNTER_CONFIGURE_V2_OPERATION_CONTRACT),
        (enable, SOURCE_COUNTER_ENABLE_V2_OPERATION_CONTRACT),
        (disable, SOURCE_COUNTER_DISABLE_V2_OPERATION_CONTRACT),
    ):
        assert spec.effect == "write"
        assert spec.required_capabilities == (contract.capability,)
        assert spec.changed_fields == ("source.input.counter",)
        assert spec.restore_coverage == "source-v2-counter-no-rollback"
        assert "no_automatic_rollback" in spec.risk_flags
    assert measure.effect == "stateful_read"
    assert measure.required_capabilities == ("source.counter_measure_v2",)
    assert measure.restore_coverage == "none-read-only"


def test_rf_source_m0_specs_are_read_only_and_exclusive() -> None:
    identity = require_operation_spec("rf_source.idn")
    snapshot = require_operation_spec("rf_source.snapshot")

    assert identity.instrument_kind == "rf_source"
    assert identity.required_capabilities == ("rf_source.idn",)
    assert identity.effect == "observe"
    assert identity.mutates is False
    assert snapshot.instrument_kind == "rf_source"
    assert snapshot.required_capabilities == ("rf_source.snapshot",)
    assert snapshot.effect == "stateful_read"
    assert snapshot.mutates is False
    assert snapshot.lease_mode == "exclusive"
    assert snapshot.restore_coverage == "none-read-only"
    assert snapshot.error_check_minimum == "disabled"


def test_rf_source_m1_cw_specs_require_snapshot_and_cw_capability() -> None:
    frequency = require_operation_spec("rf_source.set_frequency")
    power = require_operation_spec("rf_source.set_power_dbm")

    assert frequency.instrument_kind == "rf_source"
    assert frequency.required_capabilities == (
        "rf_source.snapshot",
        "rf_source.cw_configure",
    )
    assert frequency.effect == "write"
    assert frequency.changed_fields == ("rf_source.port.frequency_hz",)
    assert frequency.restore_coverage == "none"
    assert "rf_output_must_be_off" in frequency.risk_flags
    assert frequency.safe_alternatives == ("rf_source.snapshot",)
    assert power.required_capabilities == frequency.required_capabilities
    assert power.effect == "write"
    assert power.changed_fields == ("rf_source.port.power_dbm",)


def test_rf_source_m2_output_specs_require_snapshot_and_output_capability() -> None:
    enable = require_operation_spec("rf_source.output_enable")
    disable = require_operation_spec("rf_source.output_disable")

    assert enable.instrument_kind == "rf_source"
    assert enable.required_capabilities == ("rf_source.snapshot", "rf_source.output")
    assert enable.effect == "write"
    assert enable.changed_fields == ("rf_source.port.output_enabled",)
    assert enable.restore_coverage == "none"
    assert "dangerous_output" in enable.risk_flags
    assert enable.safe_alternatives == ("rf_source.snapshot",)
    assert disable.required_capabilities == enable.required_capabilities
    assert disable.effect == "write"
    assert "safe_output_disable" in disable.risk_flags


def test_rf_source_modulation_disable_spec_requires_state_evidence_and_keeps_rf_off() -> None:
    disable = require_operation_spec("rf_source.modulation_disable")

    assert disable.instrument_kind == "rf_source"
    assert disable.required_capabilities == (
        "rf_source.snapshot",
        "rf_source.modulation_disable",
    )
    assert disable.effect == "write"
    assert disable.changed_fields == (
        "rf_source.modulation.enabled_modes",
        "rf_source.modulation.global_enabled",
    )
    assert disable.restore_coverage == "none"
    assert "rf_output_must_be_off" in disable.risk_flags
    assert "safe_modulation_disable" in disable.risk_flags
    assert disable.safe_alternatives == ("rf_source.snapshot",)


def test_rf_source_pulse_configure_spec_keeps_rf_and_pulse_off() -> None:
    configure = require_operation_spec("rf_source.pulse_configure")

    assert configure.instrument_kind == "rf_source"
    assert configure.required_capabilities == (
        "rf_source.snapshot",
        "rf_source.pulse_configure",
    )
    assert configure.effect == "write"
    assert configure.changed_fields == (
        "rf_source.pulse.source",
        "rf_source.pulse.mode",
        "rf_source.pulse.period_s",
        "rf_source.pulse.width_s",
        "rf_source.pulse.polarity",
        "rf_source.pulse.state",
    )
    assert configure.restore_coverage == "none"
    assert "rf_output_must_be_off" in configure.risk_flags
    assert "pulse_state" in configure.risk_flags
    assert configure.safe_alternatives == ("rf_source.snapshot",)


def test_rf_source_sweep_configure_spec_keeps_rf_and_sweep_off() -> None:
    configure = require_operation_spec("rf_source.sweep_configure")

    assert configure.instrument_kind == "rf_source"
    assert configure.required_capabilities == (
        "rf_source.snapshot",
        "rf_source.sweep_configure",
    )
    assert configure.effect == "write"
    assert configure.changed_fields == (
        "rf_source.sweep.type",
        "rf_source.sweep.direction",
        "rf_source.sweep.shape",
        "rf_source.sweep.spacing",
        "rf_source.sweep.start_frequency_hz",
        "rf_source.sweep.stop_frequency_hz",
        "rf_source.sweep.points",
        "rf_source.sweep.dwell_s",
        "rf_source.sweep.state",
    )
    assert configure.restore_coverage == "none"
    assert "rf_output_must_be_off" in configure.risk_flags
    assert "sweep_disabled" in configure.risk_flags
    assert "trigger" not in configure.risk_flags
    assert configure.safe_alternatives == ("rf_source.snapshot",)


def test_source_v2_write_specs_match_their_static_operation_contracts() -> None:
    pairs = (
        (
            SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT,
            "source-v2-basic",
            ("source_v2", "output_must_be_off"),
        ),
        (
            SOURCE_HARMONICS_CONFIGURE_V2_OPERATION_CONTRACT,
            "source-v2-harmonics",
            ("source_v2", "output_must_be_off"),
        ),
        (
            SOURCE_HARMONICS_DISABLE_V2_OPERATION_CONTRACT,
            "source-v2-harmonics",
            ("source_v2", "output_must_be_off"),
        ),
        (
            SOURCE_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
            "source-v2-modulation",
            ("source_v2", "output_must_be_off", "am_internal_only"),
        ),
        (
            SOURCE_PM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
            "source-v2-modulation-pm",
            ("source_v2", "output_must_be_off", "pm_internal_only"),
        ),
        (
            SOURCE_FM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
            "source-v2-modulation-fm",
            ("source_v2", "output_must_be_off", "fm_internal_only"),
        ),
        (
            SOURCE_PWM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
            "source-v2-modulation-pwm",
            ("source_v2", "output_must_be_off", "pwm_internal_only"),
        ),
        (
            SOURCE_SWEEP_CONFIGURE_V2_OPERATION_CONTRACT,
            "source-v2-sweep",
            ("source_v2", "output_must_be_off", "sweep_internal_no_fire"),
        ),
        (
            SOURCE_BURST_CONFIGURE_V2_OPERATION_CONTRACT,
            "source-v2-burst",
            ("source_v2", "output_must_be_off", "burst_internal_triggered_only"),
        ),
        (
            SOURCE_PULSE_CONFIGURE_V2_OPERATION_CONTRACT,
            "source-v2-pulse",
            ("source_v2", "output_must_be_off", "pulse_width_only"),
        ),
        (
            SOURCE_ARBITRARY_STORAGE_V2_OPERATION_CONTRACT,
            "source-v2-arbitrary-storage",
            ("source_v2", "arbitrary_storage", "payload_not_artifact"),
        ),
        (
            SOURCE_ARBITRARY_SELECT_V2_OPERATION_CONTRACT,
            "source-v2-arbitrary-selection",
            ("source_v2", "output_must_be_off", "arbitrary_selection"),
        ),
        (
            SOURCE_ARBITRARY_VOLATILE_REPLACE_V2_OPERATION_CONTRACT,
            "source-v2-arbitrary-volatile",
            (
                "source_v2",
                "output_must_be_off",
                "arbitrary_volatile_replace",
                "payload_not_artifact",
                "no_retry",
            ),
        ),
        (
            SOURCE_OUTPUT_ENABLE_V2_OPERATION_CONTRACT,
            "source-v2-output",
            ("source_v2", "dangerous_output"),
        ),
        (
            SOURCE_OUTPUT_DISABLE_V2_OPERATION_CONTRACT,
            "source-v2-output",
            ("source_v2", "safe_output_off"),
        ),
    )

    for contract, restore_coverage, risk_flags in pairs:
        spec = require_operation_spec(contract.operation)
        required_fields = {field.value for field in contract.required_fields}
        changed_fields = {field.value for field in contract.changed_fields}
        postcondition_fields = {field.value for field in contract.postcondition_fields}
        cleanup_fields = {field.value for field in contract.cleanup_verification_fields}

        assert spec.instrument_kind == "source"
        assert spec.effect == "write"
        assert spec.required_capabilities == (contract.capability,)
        assert spec.lease_mode == "exclusive"
        assert spec.timeout_source == "operation.timeout_ms"
        assert spec.operation_timeout_ms == contract.operation_timeout_ms
        assert required_fields <= set(spec.required_verified_fields)
        assert required_fields <= set(spec.verification_fields)
        assert changed_fields <= set(spec.changed_fields)
        assert postcondition_fields == set(spec.postcondition_fields)
        assert cleanup_fields == set(spec.cleanup_verification_fields)
        assert spec.restore_coverage == restore_coverage
        assert spec.risk_flags == risk_flags
        assert spec.error_check_minimum == "disabled"

    assert SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_HARMONICS_CONFIGURE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_HARMONICS_DISABLE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_PM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_FM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_PWM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_BURST_CONFIGURE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_PULSE_CONFIGURE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_ARBITRARY_STORAGE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.NONE
    )
    assert SOURCE_ARBITRARY_SELECT_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_ARBITRARY_VOLATILE_REPLACE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.POTENTIAL_WHILE_OFF
    )
    assert SOURCE_OUTPUT_ENABLE_V2_OPERATION_CONTRACT.energy_effect is SourceEnergyEffect.EMIT
    assert SOURCE_OUTPUT_DISABLE_V2_OPERATION_CONTRACT.energy_effect is (
        SourceEnergyEffect.DECREASE_ONLY
    )

    for contract, configure_capability in (
        (SOURCE_BURST_FIRE_V2_OPERATION_CONTRACT, "source.burst_configure_v2"),
        (SOURCE_SWEEP_FIRE_V2_OPERATION_CONTRACT, "source.sweep_configure_v2"),
    ):
        spec = require_operation_spec(contract.operation)
        assert contract.energy_effect is SourceEnergyEffect.EMIT
        assert spec.required_capabilities == (
            contract.capability,
            configure_capability,
            "source.output_v2",
        )
        assert spec.postcondition_fields == tuple(
            field.value for field in contract.postcondition_fields
        )
        assert "persistent_session_required" in spec.risk_flags
        assert "no_retry" in spec.risk_flags


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
