from __future__ import annotations

from dataclasses import fields, replace
from math import nan
from pathlib import Path
import re

import pytest

import wavebench.instruments as public
from wavebench.errors import ConfigError
from wavebench.instruments import InstrumentDescriptor
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.source_extension_capabilities import (
    SOURCE_EXTENSION_CAPABILITY_METHODS,
    validate_source_descriptor,
    validate_source_plugin_dependencies,
)
from wavebench.instruments import source_extensions as module
from wavebench.instruments.registry import InstrumentRegistry
from wavebench.instruments.factory import open_instrument_driver
from wavebench.logging import CommandLogger
from wavebench.instruments.source_extensions import (
    Availability,
    Observed,
    SourceFeatureDirection,
    SourceFacetScope,
    SourceFieldId,
    SourceReasonCode,
    SupportState,
    source_v2_canonical_json,
)

from tests.source_v2_fixtures import (
    SourceV2FakeDriver,
    basic_facet,
    source_descriptor,
    source_extensions,
)


def test_source_public_exports_are_explicit_and_preserve_identity() -> None:
    assert module.__all__
    assert len(module.__all__) == len(set(module.__all__))
    assert all(getattr(public, name) is getattr(module, name) for name in module.__all__)
    assert "SourceSnapshotContext" not in module.__all__
    assert "SourceSnapshotContractError" not in module.__all__

    rfc = Path(
        "docs/project/rfcs/WaveBench_source能力状态与复合输出安全RFC.md"
    ).read_text(encoding="utf-8")
    match = re.search(r"R5 的精确清单为：\n\n```text\n(.*?)\n```", rfc, re.S)
    assert match is not None
    r5_exports = match.group(1).splitlines()
    assert module.__all__[: len(r5_exports)] == r5_exports
    match = re.search(r"R6／M5-A 在上述 R5 清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```", rfc, re.S)
    assert match is not None
    assert module.__all__[len(r5_exports) :] == match.group(1).splitlines()


def test_observed_preserves_missing_reason_and_rejects_nonfinite_value() -> None:
    missing = Observed.missing(
        Availability.NOT_QUERIED,
        SourceReasonCode.NOT_REQUESTED,
    )
    assert missing.value is None
    assert '"availability":"not_queried"' in source_v2_canonical_json(missing)
    assert '"reason_code":"not_requested"' in source_v2_canonical_json(missing)

    with pytest.raises(ValueError, match="non-finite"):
        Observed.value_of(nan)
    with pytest.raises(ValueError, match="reason_code"):
        Observed(Availability.UNAVAILABLE)

    evidence = "dist-info:wavebench-source-conformance/manifest.json"
    assert Observed.value_of(1.0, evidence_refs=(evidence,)).evidence_refs == (evidence,)
    with pytest.raises(ValueError, match="safe token"):
        Observed.value_of(1.0, evidence_refs=("/tmp/private.json",))


def test_resistance_bounds_require_two_finite_positive_limits() -> None:
    assert module.ResistanceBounds(49.5, 50.5).maximum_ohm == 50.5

    with pytest.raises(ValueError, match="finite"):
        module.ResistanceBounds(50.0, float("inf"))
    with pytest.raises(ValueError, match="must be >="):
        module.ResistanceBounds(50.0, 49.5)


def test_source_v2_profile_and_facet_field_shapes_are_frozen() -> None:
    expected = {
        "SourceBasicCapabilityProfile": (
            "waveform_kinds",
            "frequency_modes",
            "amplitude_units",
            "offset_readable",
            "phase_readable",
            "square_duty_readable",
        ),
        "SourceOutputCapabilityProfile": (
            "output_readable",
            "display_load_readable",
            "polarity_readable",
        ),
        "SourceHarmonicCapabilityProfile": (
            "minimum_order",
            "maximum_order",
            "amplitude_kinds",
            "completeness_modes",
        ),
        "SourceModulationCapabilityProfile": (
            "kinds",
            "sources",
            "parameter_kinds",
            "inactive_readable",
        ),
        "SourceSweepCapabilityProfile": (
            "spacing_modes",
            "trigger_sources",
            "timing_readable",
            "marker_readable",
        ),
        "SourceBurstCapabilityProfile": (
            "modes",
            "trigger_sources",
            "timing_readable",
            "gate_readable",
        ),
        "SourcePulseCapabilityProfile": (
            "hold_modes",
            "delay_readable",
            "transitions_readable",
        ),
        "SourceArbitraryCapabilityProfile": (
            "playback_modes",
            "selection_readable",
            "storage_metadata_readable",
            "sample_rate_readable",
        ),
        "SourceCounterCapabilityProfile": (
            "input_ids",
            "measurement_kinds",
            "configuration_readable",
            "query_effect",
        ),
        "SourceClockSyncCapabilityProfile": (
            "reference_clock_modes",
            "sync_readable",
            "cascade_readable",
        ),
        "SourceCrossChannelCapabilityProfile": (
            "relation_kinds",
            "supported_channel_sets",
            "relation_graph_readable",
            "shared_power_constraint_readable",
        ),
        "ResistanceBounds": ("minimum_ohm", "maximum_ohm"),
        "PortVoltageBounds": (
            "minimum_v_lower",
            "maximum_v_upper",
            "vpp_upper_v",
            "absolute_peak_upper_v",
            "rms_upper_v",
        ),
        "SafetyContributor": (
            "contributor_id",
            "feature",
            "channels",
            "minimum_v",
            "maximum_v",
            "constraint_ids",
            "proof_strength",
            "evidence_sources",
        ),
        "SourceSharedPowerBudget": (
            "participants",
            "observed_active_power_upper_w",
            "projected_power_upper_w",
            "effective_hard_limit_w",
            "constraint_ids",
            "evidence_sources",
        ),
        "CompositeOutputBudget": (
            "bounds",
            "voltage_reference_basis",
            "display_load",
            "output_source_resistance",
            "actual_termination",
            "shared_power",
            "proof_strength",
            "evidence_sources",
            "contributors",
            "blockers",
        ),
        "SourceOperationContract": (
            "operation",
            "capability",
            "feature",
            "direction",
            "energy_effect",
            "storage_effect",
            "required_fields",
            "changed_fields",
            "postcondition_fields",
            "cleanup_verification_fields",
            "v1_equivalent_routes",
            "v1_overlapping_routes",
            "operation_timeout_ms",
            "main_max_steps",
            "recovery_max_steps",
            "verification_max_steps",
        ),
        "PatchValue": ("action", "value"),
        "SourceBasicPatch": (
            "waveform_kind",
            "frequency_hz",
            "amplitude_vpp",
            "offset_v",
            "square_duty_cycle_percent",
        ),
        "SourceBasicConfigureRequest": ("channel", "patch", "mode"),
        "SourceBasicConfigureResult": ("channel", "basic", "output_enabled"),
        "SourceOutputRequest": ("channel", "enabled"),
        "SourceOutputResult": (
            "channel",
            "enabled",
            "final_amplitude",
            "final_offset_v",
        ),
        "SourceAffectedClosure": (
            "operation",
            "context_id",
            "session_epoch",
            "baseline_snapshot_digest",
            "fields",
            "required_off_outputs",
            "emergency_off_outputs",
            "restore_order",
            "non_restorable_fields",
            "closure_digest",
        ),
        "SourceVoltageReferenceConstraint": ("basis",),
        "SourceResistanceConstraint": ("resistance_ohm",),
        "SourceFrequencyDeratingBand": ("frequency_hz", "gain_upper"),
        "SourceFrequencyDeratingConstraint": ("bands",),
        "SourceModulationEnvelopeConstraint": ("kind", "gain_upper"),
        "SourceArbitraryOvershootConstraint": ("gain_upper",),
        "SourceNoisePeakConstraint": ("absolute_peak_upper_v",),
        "SourceSharedPowerConstraint": ("participants", "maximum_power_w"),
        "SourceSafetyConstraint": (
            "constraint_id",
            "kind",
            "applicability",
            "profile",
            "proof_strength",
            "evidence_refs",
        ),
        "SourceSafetyProfile": ("constraints",),
        "TerminationSpec": ("kind", "resistance_bounds"),
        "SourceTerminationEvidence": (
            "target",
            "termination",
            "source",
            "lifetime",
            "resource_fingerprint",
            "binding_digest",
            "observed_at_utc",
            "expires_at_utc",
            "evidence_ref",
        ),
        "BasicWaveFacet": (
            "waveform_kind",
            "waveform_id",
            "frequency_mode",
            "frequency_hz",
            "amplitude",
            "offset_v",
            "phase_deg",
            "square_duty_cycle_percent",
        ),
        "OutputFacet": ("enabled", "display_load", "polarity"),
        "HarmonicFacet": (
            "enabled",
            "completeness",
            "maximum_supported_order",
            "components",
        ),
        "ModulationFacet": (
            "enabled",
            "kind",
            "source",
            "parameters",
            "internal_frequency_hz",
            "internal_waveform_kind",
        ),
        "SweepFacet": (
            "enabled",
            "start_hz",
            "stop_hz",
            "spacing",
            "steps",
            "sweep_time_s",
            "start_hold_s",
            "stop_hold_s",
            "return_time_s",
            "trigger",
            "marker",
        ),
        "BurstFacet": (
            "enabled",
            "mode",
            "cycles",
            "phase_deg",
            "internal_period_s",
            "delay_s",
            "gate_polarity",
            "trigger",
        ),
        "PulseFacet": (
            "hold_basis",
            "width_s",
            "duty_cycle_percent",
            "delay_s",
            "leading_transition_s",
            "trailing_transition_s",
        ),
        "ArbitraryFacet": (
            "selected_waveform_id",
            "playback_mode",
            "playback_frequency_hz",
            "sample_rate_hz",
            "point_count",
            "storage_digest",
        ),
        "SourceSystemStateV2": (
            "counters",
            "reference_clock",
            "sync",
            "cascade",
        ),
        "SourceCrossChannelStateV2": (
            "relations",
            "relation_graph",
            "shared_power",
        ),
    }
    actual = {
        name: tuple(item.name for item in fields(getattr(module, name)))
        for name in expected
    }
    assert actual == expected


def test_source_descriptor_append_only_and_replace_compatible() -> None:
    descriptor = source_descriptor(driver=SourceV2FakeDriver(combined=True))
    names = [item.name for item in fields(InstrumentDescriptor)]

    assert names[-4:] == [
        "config_fields",
        "resource_schemes",
        "scope_extensions",
        "source_extensions",
    ]
    assert replace(descriptor, summary="changed").source_extensions is descriptor.source_extensions

    legacy = InstrumentDescriptor(
        driver_id="legacy.source",
        kind="source",
        display_name="Legacy Source",
        manufacturer="Example",
        models=("OLD1",),
        aliases=(),
        capabilities=("source.status",),
        idn_patterns=("EXAMPLE",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda context: object(),
    )
    legacy_values = [getattr(legacy, item.name) for item in fields(InstrumentDescriptor)[:-1]]
    reconstructed = InstrumentDescriptor(*legacy_values)
    assert reconstructed == legacy
    assert reconstructed.source_extensions is None


def test_source_snapshot_capability_is_additive_and_validated() -> None:
    descriptor = source_descriptor(driver=SourceV2FakeDriver(combined=True))
    expected = {
        "source.snapshot_v2": ("execute_source_query_plan_v2",),
        "source.basic_configure_v2": ("configure_source_basic_v2",),
        "source.output_v2": ("set_source_output_v2",),
    }
    assert dict(SOURCE_EXTENSION_CAPABILITY_METHODS) == expected
    assert {key: CAPABILITY_METHODS[key] for key in expected} == expected
    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, SourceV2FakeDriver(combined=True))

    with pytest.raises(TypeError, match="execute_source_query_plan_v2"):
        validate_declared_capabilities(descriptor, type("Driver", (), {"close": lambda self: None})())


def test_source_v2_basic_write_models_are_closed_and_serializable() -> None:
    keep = module.PatchValue(module.PatchAction.KEEP)
    set_frequency = module.PatchValue(module.PatchAction.SET, 1_000.0)
    patch = module.SourceBasicPatch(frequency_hz=set_frequency)
    request = module.SourceBasicConfigureRequest(channel=1, patch=patch)

    assert module.source_v2_to_data(request) == {
        "type": "SourceBasicConfigureRequest",
        "channel": 1,
        "patch": {
            "type": "SourceBasicPatch",
            "waveform_kind": {"type": "PatchValue", "action": "keep", "value": None},
            "frequency_hz": {"type": "PatchValue", "action": "set", "value": 1_000.0},
            "amplitude_vpp": {"type": "PatchValue", "action": "keep", "value": None},
            "offset_v": {"type": "PatchValue", "action": "keep", "value": None},
            "square_duty_cycle_percent": {
                "type": "PatchValue",
                "action": "keep",
                "value": None,
            },
        },
        "mode": "patch",
    }
    assert keep.action is module.PatchAction.KEEP
    assert module.SourceBasicConfigureResult(1, basic_facet(), False).output_enabled is False
    assert module.SourceOutputResult(1, False) == module.SourceOutputResult(1, False)

    with pytest.raises(ValueError, match="SET patch values"):
        module.PatchValue(module.PatchAction.SET)
    with pytest.raises(ValueError, match="KEEP patch values"):
        module.PatchValue(module.PatchAction.KEEP, 1.0)
    with pytest.raises(ValueError, match="at least one SET"):
        module.SourceBasicPatch()
    with pytest.raises(ValueError, match="must be >= 0.0"):
        module.SourceBasicPatch(amplitude_vpp=module.PatchValue(module.PatchAction.SET, -0.1))
    with pytest.raises(ValueError, match="only supports PATCH"):
        module.SourceBasicConfigureRequest(
            channel=1,
            patch=patch,
            mode=module.PatchMode.REPLACE_ALL,
        )
    with pytest.raises(ValueError, match="output_enabled=False"):
        module.SourceBasicConfigureResult(1, basic_facet(), True)
    with pytest.raises(ValueError, match="final VPP amplitude"):
        module.SourceBasicConfigureResult(
            1,
            replace(
                basic_facet(),
                amplitude=Observed.value_of(
                    module.SourceAmplitude(1.0, module.SourceAmplitudeUnit.VRMS)
                ),
            ),
            False,
        )
    with pytest.raises(ValueError, match="final offset"):
        module.SourceBasicConfigureResult(
            1,
            replace(
                basic_facet(),
                offset_v=Observed.missing(
                    Availability.NOT_QUERIED,
                    SourceReasonCode.NOT_REQUESTED,
                ),
            ),
            False,
        )
    with pytest.raises(ValueError, match="require final_amplitude"):
        module.SourceOutputResult(1, True)
    with pytest.raises(ValueError, match="must be >= 0.0"):
        module.SourceOutputResult(
            1,
            False,
            module.SourceAmplitude(-0.1, module.SourceAmplitudeUnit.VPP),
        )


def test_source_v2_write_capabilities_require_matching_directions_and_readback() -> None:
    extensions = source_extensions()
    basic, output = extensions.features
    write_extensions = replace(
        extensions,
        features=(
            replace(
                basic,
                directions=(
                    SourceFeatureDirection.CONFIGURE,
                    SourceFeatureDirection.READ,
                ),
            ),
            replace(
                output,
                directions=(
                    SourceFeatureDirection.DISABLE,
                    SourceFeatureDirection.ENABLE,
                    SourceFeatureDirection.READ,
                ),
            ),
        ),
    )
    descriptor = replace(
        source_descriptor(extensions=write_extensions),
        capabilities=(
            "source.snapshot_v2",
            "source.basic_configure_v2",
            "source.output_v2",
        ),
    )

    class WriteDriver(SourceV2FakeDriver):
        def configure_source_basic_v2(self, request):
            raise AssertionError(request)

        def set_source_output_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, WriteDriver(combined=True))

    with pytest.raises(ConfigError, match="matching declared capability"):
        validate_source_descriptor(source_descriptor(extensions=write_extensions))
    with pytest.raises(ConfigError, match="require the source.snapshot_v2"):
        validate_source_descriptor(
            replace(
                descriptor,
                capabilities=("source.basic_configure_v2",),
            )
        )
    with pytest.raises(ConfigError, match="CONFIGURE directions"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    write_extensions,
                    features=(
                        basic,
                        write_extensions.features[1],
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="matching output ENABLE and DISABLE"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    write_extensions,
                    features=(
                        write_extensions.features[0],
                        replace(
                            write_extensions.features[1],
                            directions=(
                                SourceFeatureDirection.ENABLE,
                                SourceFeatureDirection.READ,
                            ),
                        ),
                    ),
                ),
            )
        )
    with pytest.raises(TypeError, match="configure_source_basic_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingBasicWriteDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
                    "set_source_output_v2": lambda self, request: None,
                },
            )(),
        )


def test_source_v2_rejects_invalid_feature_scope_and_query_field_ownership() -> None:
    extensions = source_extensions()
    basic = extensions.features[0]
    with pytest.raises(ValueError, match="cannot use scope"):
        replace(
            basic,
            scope=SourceFacetScope.INSTRUMENT,
            channels=(),
        )

    basic_query = extensions.query_contract.facets[0]
    invalid_query = replace(
        basic_query,
        fields=(SourceFieldId.BASIC, SourceFieldId.OUTPUT),
    )
    invalid_extensions = replace(
        extensions,
        query_contract=replace(
            extensions.query_contract,
            facets=(invalid_query, *extensions.query_contract.facets[1:]),
        ),
    )
    with pytest.raises(ConfigError, match="does not belong"):
        validate_source_descriptor(source_descriptor(extensions=invalid_extensions))

    identity_query = replace(
        extensions.query_contract.facets[1],
        feature=extensions.features[-1].feature,
    )
    invalid_identity = replace(
        extensions,
        query_contract=replace(
            extensions.query_contract,
            facets=(
                extensions.query_contract.facets[0],
                extensions.query_contract.facets[2],
                identity_query,
            ),
        ),
    )
    with pytest.raises(ConfigError, match="identity facet"):
        validate_source_descriptor(source_descriptor(extensions=invalid_identity))


def test_source_v2_query_contract_cannot_probe_non_supported_feature() -> None:
    extensions = source_extensions()
    unsupported_output = replace(
        extensions.features[-1],
        support=SupportState.UNSUPPORTED,
        directions=(),
    )
    invalid_extensions = replace(
        extensions,
        features=(*extensions.features[:-1], unsupported_output),
    )

    with pytest.raises(ConfigError, match="cannot probe"):
        validate_source_descriptor(source_descriptor(extensions=invalid_extensions))


def test_source_v1_capability_mapping_is_unchanged() -> None:
    expected = {
        "source.idn": ("idn",),
        "source.errors": ("errors", "assert_no_errors"),
        "source.status": ("get_status",),
        "source.channel_profile": ("get_channel_profile",),
        "source.coupling_profile": ("get_coupling_profile",),
        "source.coupling_configure": ("configure_coupling",),
        "source.harmonic_profile": ("get_harmonic_profile",),
        "source.harmonic_configure": ("configure_harmonics",),
        "source.modulation_am_profile": ("get_am_modulation_profile",),
        "source.modulation_am_configure": ("configure_am_modulation",),
        "source.modulation_fm_profile": ("get_fm_modulation_profile",),
        "source.modulation_fm_configure": ("configure_fm_modulation",),
        "source.modulation_pm_profile": ("get_pm_modulation_profile",),
        "source.modulation_pm_configure": ("configure_pm_modulation",),
        "source.modulation_pwm_profile": ("get_pwm_modulation_profile",),
        "source.modulation_pwm_configure": ("configure_pwm_modulation",),
        "source.pulse_profile": ("get_pulse_profile",),
        "source.pulse_configure": ("configure_pulse",),
        "source.burst_profile": ("get_burst_profile",),
        "source.burst_configure": ("configure_burst",),
        "source.burst_trigger": ("trigger_burst",),
        "source.sweep_profile": ("get_sweep_profile",),
        "source.sweep_configure": ("configure_sweep",),
        "source.sweep_trigger": ("trigger_sweep",),
        "source.counter_profile": ("get_counter_profile",),
        "source.set_frequency": ("set_frequency",),
        "source.set_function": ("set_function",),
        "source.set_amplitude_vpp": ("set_amplitude_vpp",),
        "source.set_square_duty_cycle": ("set_square_duty_cycle",),
        "source.output": ("set_output",),
        "source.arbitrary_probe": ("probe_arbitrary_queries",),
        "source.arbitrary_upload": ("upload_dg4000_dac14_block",),
    }
    actual = {
        key: value
        for key, value in CAPABILITY_METHODS.items()
        if key.startswith("source.") and not key.endswith("_v2")
    }
    assert actual == expected


def test_source_v2_missing_method_closes_eager_factory_transport(monkeypatch) -> None:
    closed = {"driver": False, "transport": False}

    class InnerTransport:
        def close(self) -> None:
            closed["transport"] = True

    class MissingDriver:
        def close(self) -> None:
            closed["driver"] = True

    def factory(context):
        context.open_transport()
        return MissingDriver()

    descriptor = replace(source_descriptor(), factory=factory)
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda reference, expected_kind: descriptor,
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory.PyVisaTransport.open",
        lambda connection, logger: InnerTransport(),
    )

    with pytest.raises(ConfigError, match="execute_source_query_plan_v2"):
        open_instrument_driver(
            driver_reference="example.source-v2",
            expected_kind="source",
            resource="configured-resource",
            configured_backend="lan",
            timeout_ms=1000,
            opc_timeout_ms=1000,
            read_retry_attempts=0,
            read_retry_delay_ms=0,
            logger=CommandLogger(),
        )
    assert closed == {"driver": True, "transport": True}


def test_source_snapshot_descriptor_requires_pep440_floor() -> None:
    descriptor = source_descriptor(driver=SourceV2FakeDriver(combined=True))
    with pytest.raises(ConfigError, match="0.8.24"):
        validate_source_descriptor(replace(descriptor, wavebench_min_version="0.8.23"))
    with pytest.raises(ConfigError, match="PEP 440"):
        validate_source_descriptor(replace(descriptor, wavebench_min_version="not-a-version"))
    with pytest.raises(ConfigError, match="min < max"):
        validate_source_descriptor(
            replace(
                descriptor,
                wavebench_min_version="0.9.0",
                wavebench_max_version="0.9.0",
            )
        )


def test_source_v2_wheel_dependency_must_match_descriptor_interval() -> None:
    descriptor = source_descriptor(driver=SourceV2FakeDriver(combined=True))

    validate_source_plugin_dependencies(
        descriptor,
        ("wavebench>=0.8.24,<0.9",),
    )
    validate_source_plugin_dependencies(
        descriptor,
        (
            "wavebench>=0.8.24,<0.9,!=0.8.25",
            'wavebench>=99; python_version < "3.0"',
        ),
    )

    with pytest.raises(ConfigError, match="explicitly include >=0.8.24,<0.9.0"):
        validate_source_plugin_dependencies(descriptor, ("wavebench>=0.8,<0.9",))
    with pytest.raises(ConfigError, match="explicitly include >=0.8.24,<0.9.0"):
        validate_source_plugin_dependencies(descriptor, ("wavebench>=0.8.24,<1.0",))
    with pytest.raises(ConfigError, match="expands or excludes"):
        validate_source_plugin_dependencies(
            descriptor,
            ("wavebench>=0.8.24,<0.9,!=0.8.24",),
        )
    with pytest.raises(ConfigError, match="exactly one active"):
        validate_source_plugin_dependencies(
            descriptor,
            ('wavebench>=0.8.24,<0.9; python_version < "3.0"',),
        )
    with pytest.raises(ConfigError, match="exactly one"):
        validate_source_plugin_dependencies(
            descriptor,
            ("wavebench>=0.8.24,<0.9", "wavebench>=0.8.24,<0.9"),
        )
    with pytest.raises(ConfigError, match="invalid Requires-Dist"):
        validate_source_plugin_dependencies(descriptor, ("wavebench=>not-a-version",))


def test_registry_rejects_invalid_source_v2_descriptor_before_factory() -> None:
    calls = []
    descriptor = replace(
        source_descriptor(driver=SourceV2FakeDriver(combined=True)),
        factory=lambda context: calls.append(context),
        wavebench_min_version="0.8.23",
    )
    registry = InstrumentRegistry(builtins=(descriptor,))

    with pytest.raises(ConfigError, match="0.8.24"):
        registry.resolve("example.source-v2", expected_kind="source")
    assert calls == []
