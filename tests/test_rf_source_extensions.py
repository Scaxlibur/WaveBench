from __future__ import annotations

from dataclasses import replace

import pytest

from wavebench.errors import ConfigError
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.rf_source_capabilities import (
    RF_SOURCE_CAPABILITY_METHODS,
    validate_rf_source_descriptor,
    validate_rf_source_plugin_dependencies,
)
from wavebench.instruments.rf_source_extensions import (
    RF_SOURCE_CONTRACT_VERSION,
    RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
    RF_SOURCE_SNAPSHOT_SCHEMA,
    RfAvailability,
    RfCwProfile,
    RfCwRequest,
    RfCwResult,
    RfFeature,
    RfFeatureCapability,
    RfFeatureDirection,
    RfModulationState,
    RfObserved,
    RfOutputPortProfile,
    RfOutputProfile,
    RfOutputRequest,
    RfOutputResult,
    RfPortSnapshot,
    RfProtectionConditionPolicy,
    RfProtectionStatus,
    RfPulseState,
    RfReasonCode,
    RfSourceDescriptorExtensions,
    RfSourceSnapshot,
    RfSourceTopology,
    RfSweepState,
    rf_source_snapshot_document,
    rf_source_snapshot_operation_artifact,
    rf_source_cw_operation_artifact,
    rf_source_output_operation_artifact,
)


class RfDriver:
    def close(self) -> None:
        pass

    def idn(self) -> str:
        return "EXAMPLE,RF1,0,1"

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        return snapshot()

    def configure_cw(self, request: RfCwRequest) -> None:
        del request

    def set_rf_output(self, request: RfOutputRequest) -> None:
        del request


def topology() -> RfSourceTopology:
    return RfSourceTopology(
        (
            RfOutputPortProfile(
                port_id="rf_out",
                frequency_min_hz=9_000.0,
                frequency_max_hz=3_000_000_000.0,
                power_min_dbm=-110.0,
                power_max_dbm=20.0,
                power_reference_impedance_ohm=50.0,
            ),
        )
    )


def extensions() -> RfSourceDescriptorExtensions:
    return RfSourceDescriptorExtensions(
        contract_version=RF_SOURCE_CONTRACT_VERSION,
        topology=topology(),
        features=(
            RfFeatureCapability(
                feature=RfFeature.CW,
                directions=(RfFeatureDirection.READ,),
                port_ids=("rf_out",),
                profile=RfCwProfile(frequency_readable=True, power_readable=True),
            ),
            RfFeatureCapability(
                feature=RfFeature.OUTPUT,
                directions=(RfFeatureDirection.READ,),
                port_ids=("rf_out",),
                profile=RfOutputProfile(output_readable=True),
            ),
        ),
        protection_conditions=(
            RfProtectionConditionPolicy("overtemperature", blocks_output_enable=True),
        ),
    )


def cw_extensions() -> RfSourceDescriptorExtensions:
    return RfSourceDescriptorExtensions(
        contract_version=RF_SOURCE_CONTRACT_VERSION,
        topology=topology(),
        features=(
            RfFeatureCapability(
                feature=RfFeature.CW,
                directions=(RfFeatureDirection.CONFIGURE, RfFeatureDirection.READ),
                port_ids=("rf_out",),
                profile=RfCwProfile(
                    frequency_readable=True,
                    power_readable=True,
                    frequency_configurable=True,
                    power_configurable=True,
                ),
            ),
        ),
    )


def output_extensions() -> RfSourceDescriptorExtensions:
    return RfSourceDescriptorExtensions(
        contract_version=RF_SOURCE_CONTRACT_VERSION,
        topology=topology(),
        features=(
            RfFeatureCapability(
                feature=RfFeature.OUTPUT,
                directions=(
                    RfFeatureDirection.DISABLE,
                    RfFeatureDirection.ENABLE,
                    RfFeatureDirection.READ,
                ),
                port_ids=("rf_out",),
                profile=RfOutputProfile(output_readable=True),
            ),
        ),
    )


def descriptor(**changes: object) -> InstrumentDescriptor:
    value = InstrumentDescriptor(
        driver_id="example.rf1",
        kind="rf_source",
        display_name="Example RF Source",
        manufacturer="Example",
        models=("RF1",),
        aliases=(),
        capabilities=("rf_source.idn", "rf_source.snapshot"),
        idn_patterns=("EXAMPLE,RF1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda context: RfDriver(),
        wavebench_min_version="0.8.25",
        wavebench_max_version="0.9.0",
        rf_source_extensions=extensions(),
    )
    return replace(value, **changes)


def snapshot() -> RfSourceSnapshot:
    return RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(1_000_000.0),
                power_dbm=RfObserved.value_of(-30.0),
                output_enabled=RfObserved.value_of(False),
                modulation=RfObserved.value_of(RfModulationState.DISABLED),
                pulse=RfObserved.value_of(RfPulseState.DISABLED),
                sweep=RfObserved.value_of(RfSweepState.DISABLED),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
    )


def test_rf_source_topology_and_features_are_strict() -> None:
    with pytest.raises(ValueError, match="finite"):
        RfOutputPortProfile("rf_out", float("nan"), 1.0, -1.0, 1.0, 50.0)
    with pytest.raises(ValueError, match="must be positive"):
        RfOutputPortProfile("rf_out", 1.0, 2.0, -1.0, 1.0, 0.0)
    with pytest.raises(ValueError, match="sorted and unique"):
        RfSourceTopology(
            (
                RfOutputPortProfile("z", 1.0, 2.0, -1.0, 1.0, 50.0),
                RfOutputPortProfile("a", 1.0, 2.0, -1.0, 1.0, 50.0),
            )
        )
    with pytest.raises(ValueError, match="does not match feature"):
        RfFeatureCapability(
            feature=RfFeature.CW,
            directions=(RfFeatureDirection.READ,),
            port_ids=("rf_out",),
            profile=RfOutputProfile(output_readable=True),
        )
    with pytest.raises(ValueError, match="requires readable frequency"):
        RfCwProfile(
            frequency_readable=False,
            power_readable=True,
            frequency_configurable=True,
        )
    with pytest.raises(ValueError, match="unknown port"):
        replace(
            extensions(),
            features=(
                RfFeatureCapability(
                    feature=RfFeature.CW,
                    directions=(RfFeatureDirection.READ,),
                    port_ids=("other",),
                    profile=RfCwProfile(frequency_readable=True, power_readable=True),
                ),
            ),
        )


def test_rf_observation_and_snapshot_reject_unsafe_values() -> None:
    with pytest.raises(ValueError, match="must carry a value"):
        RfObserved(RfAvailability.VALUE)
    with pytest.raises(ValueError, match="require a registered reason_code"):
        RfObserved(RfAvailability.UNKNOWN)
    with pytest.raises(ValueError, match="cannot contain non-finite"):
        RfObserved.value_of(float("inf"))
    with pytest.raises(ValueError, match="invalid VALUE type"):
        RfPortSnapshot(
            port_id="rf_out",
            frequency_hz=RfObserved.value_of(1.0),
            power_dbm=RfObserved.value_of(0.0),
            output_enabled=RfObserved.value_of("ON"),
            modulation=RfObserved.missing(RfAvailability.UNSUPPORTED, RfReasonCode.DESCRIPTOR_UNSUPPORTED),
            pulse=RfObserved.missing(RfAvailability.UNSUPPORTED, RfReasonCode.DESCRIPTOR_UNSUPPORTED),
            sweep=RfObserved.missing(RfAvailability.UNSUPPORTED, RfReasonCode.DESCRIPTOR_UNSUPPORTED),
        )


def test_rf_cw_request_and_result_require_one_finite_field() -> None:
    assert RfCwRequest(port_id="rf_out", frequency_hz=1_000_000.0).frequency_hz == 1_000_000.0
    assert RfCwResult(port_id="rf_out", power_dbm=-20.0).power_dbm == -20.0
    with pytest.raises(ValueError, match="exactly one"):
        RfCwRequest(port_id="rf_out")
    with pytest.raises(ValueError, match="exactly one"):
        RfCwRequest(port_id="rf_out", frequency_hz=1.0, power_dbm=0.0)
    with pytest.raises(ValueError, match="finite"):
        RfCwResult(port_id="rf_out", frequency_hz=float("nan"))


def test_rf_output_request_and_result_require_explicit_boolean_state() -> None:
    assert RfOutputRequest(port_id="rf_out", enabled=True).enabled is True
    assert RfOutputResult(port_id="rf_out", enabled=False, write_completed=False).write_completed is False
    with pytest.raises(ValueError, match="boolean"):
        RfOutputRequest(port_id="rf_out", enabled=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="boolean"):
        RfOutputResult(port_id="rf_out", enabled=False, write_completed=0)  # type: ignore[arg-type]


def test_rf_snapshot_document_and_artifact_are_structured_and_redacted() -> None:
    value = snapshot()
    document = rf_source_snapshot_document(value)
    artifact = rf_source_snapshot_operation_artifact(value)

    assert document["schema"] == RF_SOURCE_SNAPSHOT_SCHEMA
    assert document["ports"][0]["frequency_hz"]["value"] == 1_000_000.0
    assert artifact == {
        "schema": RF_SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "rf_source.snapshot",
        "snapshot": document,
    }


def test_rf_cw_operation_artifact_uses_typed_pre_and_postcondition_evidence() -> None:
    request = RfCwRequest(port_id="rf_out", frequency_hz=2_000_000.0)
    result = RfCwResult(port_id="rf_out", frequency_hz=2_000_000.0)
    preflight = snapshot()
    postcondition = RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(2_000_000.0),
                power_dbm=RfObserved.value_of(-30.0),
                output_enabled=RfObserved.value_of(False),
                modulation=RfObserved.value_of(RfModulationState.DISABLED),
                pulse=RfObserved.value_of(RfPulseState.DISABLED),
                sweep=RfObserved.value_of(RfSweepState.DISABLED),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
    )

    artifact = rf_source_cw_operation_artifact(
        request,
        result,
        preflight_snapshot=preflight,
        postcondition_snapshot=postcondition,
    )

    assert artifact["schema"] == RF_SOURCE_OPERATION_ARTIFACT_SCHEMA
    assert artifact["operation"] == "rf_source.set_frequency"
    assert artifact["request"]["frequency_hz"] == 2_000_000.0
    assert artifact["result"]["frequency_hz"] == 2_000_000.0
    assert "resource" not in str(artifact)


def test_rf_output_operation_artifact_uses_typed_pre_and_postcondition_evidence() -> None:
    request = RfOutputRequest(port_id="rf_out", enabled=True)
    result = RfOutputResult(port_id="rf_out", enabled=True, write_completed=True)
    preflight = snapshot()
    postcondition = replace(
        preflight,
        ports=(replace(preflight.ports[0], output_enabled=RfObserved.value_of(True)),),
    )

    artifact = rf_source_output_operation_artifact(
        request,
        result,
        preflight_snapshot=preflight,
        postcondition_snapshot=postcondition,
    )

    assert artifact["operation"] == "rf_source.output_enable"
    assert artifact["result"]["write_completed"] is True
    assert artifact["postcondition_snapshot"]["ports"][0]["output_enabled"]["value"] is True
    with pytest.raises(ValueError, match="same target"):
        rf_source_output_operation_artifact(
            request,
            RfOutputResult(port_id="rf_out", enabled=False, write_completed=True),
            preflight_snapshot=preflight,
            postcondition_snapshot=postcondition,
        )


def test_rf_descriptor_capabilities_and_driver_methods_are_validated() -> None:
    value = descriptor()

    assert dict(RF_SOURCE_CAPABILITY_METHODS) == {
        "rf_source.idn": ("idn",),
        "rf_source.snapshot": ("get_rf_snapshot",),
        "rf_source.cw_configure": ("configure_cw",),
        "rf_source.output": ("set_rf_output",),
    }
    assert {key: CAPABILITY_METHODS[key] for key in RF_SOURCE_CAPABILITY_METHODS} == dict(
        RF_SOURCE_CAPABILITY_METHODS
    )
    validate_rf_source_descriptor(value)
    validate_declared_capabilities(value, RfDriver())

    with pytest.raises(TypeError, match="get_rf_snapshot"):
        validate_declared_capabilities(
            value,
            type("IdentityOnly", (), {"close": lambda self: None, "idn": lambda self: "idn"})(),
        )
    with pytest.raises(ConfigError, match="unknown capabilities"):
        validate_rf_source_descriptor(replace(value, capabilities=("rf_source.idn", "rf_source.future")))
    with pytest.raises(ConfigError, match="CW feature"):
        validate_rf_source_descriptor(
            replace(
                value,
                capabilities=("rf_source.idn", "rf_source.snapshot", "rf_source.cw_configure"),
            )
        )
    cw_descriptor = replace(
        value,
        capabilities=("rf_source.idn", "rf_source.snapshot", "rf_source.cw_configure"),
        rf_source_extensions=cw_extensions(),
    )
    validate_rf_source_descriptor(cw_descriptor)
    validate_declared_capabilities(cw_descriptor, RfDriver())
    with pytest.raises(ConfigError, match="output ENABLE and DISABLE"):
        validate_rf_source_descriptor(
            replace(
                value,
                capabilities=("rf_source.idn", "rf_source.snapshot", "rf_source.output"),
            )
        )
    output_descriptor = replace(
        value,
        capabilities=("rf_source.idn", "rf_source.snapshot", "rf_source.output"),
        rf_source_extensions=output_extensions(),
    )
    validate_rf_source_descriptor(output_descriptor)
    validate_declared_capabilities(output_descriptor, RfDriver())


def test_rf_source_kind_requires_extensions_and_uses_append_only_field() -> None:
    with pytest.raises(ValueError, match="require rf_source_extensions"):
        InstrumentDescriptor(
            driver_id="example.missing",
            kind="rf_source",
            display_name="Missing",
            manufacturer="Example",
            models=("RF1",),
            aliases=(),
            capabilities=("rf_source.idn",),
            idn_patterns=(),
            backends=("pyvisa",),
            option_specs=(),
            permissions=("instrument.io",),
            factory=lambda context: RfDriver(),
        )
    with pytest.raises(ConfigError, match="require the rf_source.idn"):
        validate_rf_source_descriptor(replace(descriptor(), capabilities=("rf_source.snapshot",)))


def test_rf_source_wheel_dependency_must_match_descriptor_interval() -> None:
    value = descriptor()

    validate_rf_source_plugin_dependencies(value, ("wavebench>=0.8.25,<0.9",))
    validate_rf_source_plugin_dependencies(
        value,
        (
            "wavebench>=0.8.25,<0.9,!=0.8.26",
            'wavebench>=99; python_version < "3.0"',
        ),
    )

    with pytest.raises(ConfigError, match="explicitly include >=0.8.25,<0.9.0"):
        validate_rf_source_plugin_dependencies(value, ("wavebench>=0.8,<0.9",))
    with pytest.raises(ConfigError, match="expands or excludes"):
        validate_rf_source_plugin_dependencies(
            value,
            ("wavebench>=0.8.25,<0.9,!=0.8.25",),
        )
    with pytest.raises(ConfigError, match="exactly one active"):
        validate_rf_source_plugin_dependencies(
            value,
            ('wavebench>=0.8.25,<0.9; python_version < "3.0"',),
        )
    with pytest.raises(ConfigError, match="invalid Requires-Dist"):
        validate_rf_source_plugin_dependencies(value, ("wavebench=>not-a-version",))
