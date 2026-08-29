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
    source_extensions_with_harmonics,
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
    m5_exports = match.group(1).splitlines()
    assert module.__all__[len(r5_exports) : len(r5_exports) + len(m5_exports)] == m5_exports
    match = re.search(r"M6-A／Harmonic 在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```", rfc, re.S)
    assert match is not None
    harmonic_exports = match.group(1).splitlines()
    harmonic_start = len(r5_exports) + len(m5_exports)
    assert module.__all__[harmonic_start : harmonic_start + len(harmonic_exports)] == harmonic_exports
    match = re.search(r"M6-A／内部 AM 调制在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```", rfc, re.S)
    assert match is not None
    modulation_exports = match.group(1).splitlines()
    modulation_start = harmonic_start + len(harmonic_exports)
    assert module.__all__[modulation_start : modulation_start + len(modulation_exports)] == modulation_exports
    match = re.search(r"M6-A／WIDTH Pulse 在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```", rfc, re.S)
    assert match is not None
    pulse_exports = match.group(1).splitlines()
    pulse_start = modulation_start + len(modulation_exports)
    assert module.__all__[pulse_start : pulse_start + len(pulse_exports)] == pulse_exports
    match = re.search(r"M6-A／内部 PM 调制在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```", rfc, re.S)
    assert match is not None
    pm_exports = match.group(1).splitlines()
    pm_start = pulse_start + len(pulse_exports)
    assert module.__all__[pm_start : pm_start + len(pm_exports)] == pm_exports
    match = re.search(
        r"M6-A／内部 Triggered Burst 在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```",
        rfc,
        re.S,
    )
    assert match is not None
    burst_exports = match.group(1).splitlines()
    burst_start = pm_start + len(pm_exports)
    assert module.__all__[burst_start : burst_start + len(burst_exports)] == burst_exports
    match = re.search(r"M6-A／内部 FM 调制在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```", rfc, re.S)
    assert match is not None
    fm_exports = match.group(1).splitlines()
    fm_start = burst_start + len(burst_exports)
    assert module.__all__[fm_start : fm_start + len(fm_exports)] == fm_exports
    match = re.search(r"M6-A／内部 PWM 调制在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```", rfc, re.S)
    assert match is not None
    pwm_exports = match.group(1).splitlines()
    pwm_start = fm_start + len(fm_exports)
    assert module.__all__[pwm_start : pwm_start + len(pwm_exports)] == pwm_exports
    match = re.search(r"M6-A／内部 Sweep 在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```", rfc, re.S)
    assert match is not None
    sweep_exports = match.group(1).splitlines()
    sweep_start = pwm_start + len(pwm_exports)
    assert module.__all__[sweep_start : sweep_start + len(sweep_exports)] == sweep_exports
    match = re.search(
        r"M6-B／ARB storage 与 selection 在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```",
        rfc,
        re.S,
    )
    assert match is not None
    arbitrary_exports = match.group(1).splitlines()
    arbitrary_start = sweep_start + len(sweep_exports)
    assert module.__all__[arbitrary_start : arbitrary_start + len(arbitrary_exports)] == arbitrary_exports
    match = re.search(
        r"M6-C／跨通道关系在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```",
        rfc,
        re.S,
    )
    assert match is not None
    relation_exports = match.group(1).splitlines()
    relation_start = arbitrary_start + len(arbitrary_exports)
    assert module.__all__[relation_start : relation_start + len(relation_exports)] == relation_exports
    match = re.search(
        r"首次稳定版 Coupling 只读模型修正在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```",
        rfc,
        re.S,
    )
    assert match is not None
    coupling_exports = match.group(1).splitlines()
    coupling_start = relation_start + len(relation_exports)
    assert module.__all__[coupling_start : coupling_start + len(coupling_exports)] == coupling_exports
    match = re.search(
        r"首次稳定版 Sync 只读模型修正在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```",
        rfc,
        re.S,
    )
    assert match is not None
    sync_exports = match.group(1).splitlines()
    sync_start = coupling_start + len(coupling_exports)
    assert module.__all__[sync_start : sync_start + len(sync_exports)] == sync_exports
    match = re.search(
        r"首次稳定版 Noise Overlay 只读模型在上述清单末尾追加以下精确条目：\n\n```text\n(.*?)\n```",
        rfc,
        re.S,
    )
    assert match is not None
    assert module.__all__[sync_start + len(sync_exports) :] == match.group(1).splitlines()


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
        "SourceNoiseOverlayCapabilityProfile": (
            "enabled_readable",
            "scale_kinds",
        ),
        "SourceNoiseOverlayScale": ("kind", "value"),
        "NoiseOverlayFacet": ("enabled", "scales"),
        "SourceHarmonicCapabilityProfile": (
            "minimum_order",
            "maximum_order",
            "amplitude_kinds",
            "completeness_modes",
            "presets",
            "configured_order_readable",
            "preset_readable",
        ),
        "SourceModulationCapabilityProfile": (
            "kinds",
            "sources",
            "parameter_kinds",
            "inactive_readable",
            "configuration_readable",
        ),
        "SourceSweepCapabilityProfile": (
            "spacing_modes",
            "trigger_sources",
            "timing_readable",
            "marker_readable",
            "configuration_readable",
        ),
        "SourceBurstCapabilityProfile": (
            "modes",
            "trigger_sources",
            "timing_readable",
            "gate_readable",
            "triggered_internal_configuration_readable",
        ),
        "SourcePulseCapabilityProfile": (
            "hold_modes",
            "delay_readable",
            "transitions_readable",
            "width_configuration_readable",
        ),
        "SourceArbitraryCapabilityProfile": (
            "playback_modes",
            "selection_readable",
            "storage_metadata_readable",
            "sample_rate_readable",
            "storage_slot_metadata_readable",
            "storage_write_modes",
            "storage_max_payload_bytes",
        ),
        "SourceCounterCapabilityProfile": (
            "input_ids",
            "measurement_kinds",
            "configuration_readable",
            "query_effect",
        ),
        "SourceCouplingCapabilityProfile": (
            "dimensions",
            "parameter_kinds",
            "supported_channel_sets",
            "global_state_readable",
            "reference_channel_readable",
            "relation_graph_readable",
            "configuration_readable",
        ),
        "SourceCouplingParameter": ("kind", "value"),
        "SourceCouplingDimensionState": ("dimension", "enabled", "parameter"),
        "SourceCouplingState": (
            "feature",
            "channels",
            "enabled",
            "reference_channel",
            "dimensions",
        ),
        "SourceReferenceClockCapabilityProfile": (
            "modes",
            "frequency_readable",
            "lock_state_readable",
        ),
        "SourceSyncCapabilityProfile": (
            "enabled_readable",
            "polarity_readable",
            "source_channel_readable",
            "source_channels",
        ),
        "SourceCascadeCapabilityProfile": (
            "enabled_readable",
            "role_readable",
        ),
        "SourceCrossChannelCapabilityProfile": (
            "relation_kinds",
            "supported_channel_sets",
            "relation_graph_readable",
            "shared_power_constraint_readable",
            "configuration_readable",
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
        "SourceHarmonicConfigureRequest": ("channel", "order", "preset"),
        "SourceHarmonicConfigureResult": ("channel", "harmonics", "output_enabled"),
        "SourceHarmonicDisableRequest": ("channel",),
        "SourceHarmonicDisableResult": ("channel", "harmonics", "output_enabled"),
        "SourceModulationConfigureRequest": (
            "channel",
            "depth_percent",
            "internal_frequency_hz",
        ),
        "SourceModulationConfigureResult": ("channel", "modulation", "output_enabled"),
        "SourcePmModulationConfigureRequest": (
            "channel",
            "phase_deviation_deg",
            "internal_frequency_hz",
        ),
        "SourcePmModulationConfigureResult": ("channel", "modulation", "output_enabled"),
        "SourceBurstConfigureRequest": (
            "channel",
            "cycles",
            "phase_deg",
            "internal_period_s",
            "delay_s",
        ),
        "SourceBurstConfigureResult": ("channel", "burst", "output_enabled"),
        "SourceFmModulationConfigureRequest": (
            "channel",
            "frequency_deviation_hz",
            "internal_frequency_hz",
        ),
        "SourceFmModulationConfigureResult": ("channel", "modulation", "output_enabled"),
        "SourcePwmModulationConfigureRequest": (
            "channel",
            "internal_frequency_hz",
            "duty_deviation_percent",
            "width_deviation_s",
        ),
        "SourcePwmModulationConfigureResult": ("channel", "modulation", "output_enabled"),
        "SourceSweepConfigureRequest": (
            "channel",
            "start_hz",
            "stop_hz",
            "spacing",
            "steps",
            "sweep_time_s",
        ),
        "SourceSweepConfigureResult": ("channel", "basic", "sweep", "output_enabled"),
        "SourcePulseConfigureRequest": (
            "channel",
            "width_s",
            "delay_s",
            "leading_transition_s",
            "trailing_transition_s",
        ),
        "SourcePulseConfigureResult": ("channel", "pulse", "output_enabled"),
        "SourceRelationOutputState": ("channel", "enabled"),
        "SourceCrossChannelConfigureResult": (
            "feature",
            "channels",
            "enabled",
            "relation",
            "outputs",
        ),
        "SourceCombineConfigureRequest": ("channels", "enabled"),
        "SourceCouplingConfigureRequest": ("channels", "enabled"),
        "SourceTrackingConfigureRequest": ("channels", "enabled"),
        "SourcePhaseRelationConfigureRequest": ("channels", "enabled"),
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
            "configured_order",
            "preset",
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
            "cascade",
        ),
        "SourceChannelStateV2": (
            "channel",
            "basic",
            "output",
            "harmonics",
            "modulation",
            "sweep",
            "burst",
            "pulse",
            "arbitrary",
            "sync",
            "noise_overlay",
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

    assert names[-5:] == [
        "config_fields",
        "resource_schemes",
        "scope_extensions",
        "source_extensions",
        "rf_source_extensions",
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
        "source.harmonics_configure_v2": ("configure_source_harmonics_v2",),
        "source.harmonics_disable_v2": ("disable_source_harmonics_v2",),
        "source.modulation_configure_v2": ("configure_source_modulation_v2",),
        "source.pulse_configure_v2": ("configure_source_pulse_v2",),
        "source.modulation_pm_configure_v2": ("configure_source_pm_modulation_v2",),
        "source.burst_configure_v2": ("configure_source_burst_v2",),
        "source.modulation_fm_configure_v2": ("configure_source_fm_modulation_v2",),
        "source.modulation_pwm_configure_v2": ("configure_source_pwm_modulation_v2",),
            "source.sweep_configure_v2": ("configure_source_sweep_v2",),
            "source.output_v2": ("set_source_output_v2",),
            "source.arbitrary_storage_v2": (
                "read_source_arbitrary_storage_v2",
                "mutate_source_arbitrary_storage_v2",
            ),
            "source.arbitrary_select_v2": ("select_source_arbitrary_v2",),
            "source.combine_configure_v2": ("configure_source_combine_v2",),
            "source.coupling_configure_v2": ("configure_source_coupling_v2",),
            "source.tracking_configure_v2": ("configure_source_tracking_v2",),
            "source.phase_relation_configure_v2": (
                "configure_source_phase_relation_v2",
            ),
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
    with pytest.raises(ValueError, match="arbitrary or other"):
        module.SourceBasicPatch(
            waveform_kind=module.PatchValue(
                module.PatchAction.SET,
                module.SourceWaveformKind.ARBITRARY,
            )
        )
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


def test_source_v2_harmonic_write_models_are_closed_and_serializable() -> None:
    harmonics = module.HarmonicFacet(
        enabled=Observed.value_of(True),
        completeness=Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        ),
        maximum_supported_order=Observed.value_of(16),
        components=Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        ),
        configured_order=Observed.value_of(8),
        preset=Observed.value_of(module.SourceHarmonicPreset.ODD),
    )
    request = module.SourceHarmonicConfigureRequest(
        channel=1,
        order=8,
        preset=module.SourceHarmonicPreset.ODD,
    )
    result = module.SourceHarmonicConfigureResult(1, harmonics, False)

    assert module.source_v2_to_data(request) == {
        "type": "SourceHarmonicConfigureRequest",
        "channel": 1,
        "order": 8,
        "preset": "odd",
    }
    assert result.harmonics.configured_order.value == 8
    assert result.harmonics.preset.value is module.SourceHarmonicPreset.ODD
    with pytest.raises(ValueError, match="must be >= 2"):
        module.SourceHarmonicConfigureRequest(
            channel=1,
            order=1,
            preset=module.SourceHarmonicPreset.ALL,
        )
    with pytest.raises(ValueError, match="invalid type"):
        module.SourceHarmonicConfigureRequest(channel=1, order=8, preset="ODD")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="output_enabled=False"):
        module.SourceHarmonicConfigureResult(1, harmonics, True)
    with pytest.raises(ValueError, match="configured_order readback"):
        module.SourceHarmonicConfigureResult(
            1,
            replace(
                harmonics,
                configured_order=Observed.missing(
                    Availability.NOT_QUERIED,
                    SourceReasonCode.NOT_REQUESTED,
                ),
            ),
            False,
        )


def test_source_v2_harmonic_disable_models_are_closed_and_serializable() -> None:
    disabled_harmonics = module.HarmonicFacet(
        enabled=Observed.value_of(False),
        completeness=Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        ),
        maximum_supported_order=Observed.value_of(16),
        components=Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        ),
        configured_order=Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        ),
        preset=Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        ),
    )
    request = module.SourceHarmonicDisableRequest(channel=1)
    result = module.SourceHarmonicDisableResult(1, disabled_harmonics, False)

    assert module.source_v2_to_data(request) == {
        "type": "SourceHarmonicDisableRequest",
        "channel": 1,
    }
    assert result.harmonics.enabled.value is False
    with pytest.raises(ValueError, match="must be >= 1"):
        module.SourceHarmonicDisableRequest(channel=0)
    with pytest.raises(ValueError, match="output_enabled=False"):
        module.SourceHarmonicDisableResult(1, disabled_harmonics, True)
    with pytest.raises(ValueError, match="disabled harmonic readback"):
        module.SourceHarmonicDisableResult(
            1,
            replace(disabled_harmonics, enabled=Observed.value_of(True)),
            False,
        )


def test_source_v2_modulation_write_models_are_closed_and_serializable() -> None:
    modulation = module.ModulationFacet(
        enabled=Observed.value_of(True),
        kind=Observed.value_of(module.SourceModulationKind.AM),
        source=Observed.value_of(module.SourceModulationSource.INTERNAL),
        parameters=Observed.value_of(
            (
                module.SourceModulationParameter(
                    module.SourceModulationParameterKind.DEPTH_PERCENT,
                    80.0,
                ),
            )
        ),
        internal_frequency_hz=Observed.value_of(25.0),
        internal_waveform_kind=Observed.value_of(module.SourceWaveformKind.SINE),
    )
    request = module.SourceModulationConfigureRequest(
        channel=1,
        depth_percent=80.0,
        internal_frequency_hz=25.0,
    )
    result = module.SourceModulationConfigureResult(1, modulation, False)

    assert module.source_v2_to_data(request) == {
        "type": "SourceModulationConfigureRequest",
        "channel": 1,
        "depth_percent": 80.0,
        "internal_frequency_hz": 25.0,
    }
    assert result.modulation.kind.value is module.SourceModulationKind.AM
    with pytest.raises(ValueError, match="must be <= 100.0"):
        module.SourceModulationConfigureRequest(1, 100.1, 25.0)
    with pytest.raises(ValueError, match="must be > 0"):
        module.SourceModulationConfigureRequest(1, 80.0, 0.0)
    with pytest.raises(ValueError, match="output_enabled=False"):
        module.SourceModulationConfigureResult(1, modulation, True)
    with pytest.raises(ValueError, match="internal sine readback"):
        module.SourceModulationConfigureResult(
            1,
            replace(
                modulation,
                internal_waveform_kind=Observed.value_of(module.SourceWaveformKind.SQUARE),
            ),
            False,
        )


def test_source_v2_pm_modulation_write_models_are_closed_and_serializable() -> None:
    modulation = module.ModulationFacet(
        enabled=Observed.value_of(True),
        kind=Observed.value_of(module.SourceModulationKind.PM),
        source=Observed.value_of(module.SourceModulationSource.INTERNAL),
        parameters=Observed.value_of(
            (
                module.SourceModulationParameter(
                    module.SourceModulationParameterKind.PHASE_DEVIATION_DEG,
                    90.0,
                ),
            )
        ),
        internal_frequency_hz=Observed.value_of(25.0),
        internal_waveform_kind=Observed.value_of(module.SourceWaveformKind.SINE),
    )
    request = module.SourcePmModulationConfigureRequest(
        channel=1,
        phase_deviation_deg=90.0,
        internal_frequency_hz=25.0,
    )
    result = module.SourcePmModulationConfigureResult(1, modulation, False)

    assert module.source_v2_to_data(request) == {
        "type": "SourcePmModulationConfigureRequest",
        "channel": 1,
        "phase_deviation_deg": 90.0,
        "internal_frequency_hz": 25.0,
    }
    assert result.modulation.kind.value is module.SourceModulationKind.PM
    with pytest.raises(ValueError, match="must be <= 360.0"):
        module.SourcePmModulationConfigureRequest(1, 360.1, 25.0)
    with pytest.raises(ValueError, match="must be > 0"):
        module.SourcePmModulationConfigureRequest(1, 90.0, 0.0)
    with pytest.raises(ValueError, match="output_enabled=False"):
        module.SourcePmModulationConfigureResult(1, modulation, True)
    with pytest.raises(ValueError, match="PM readback"):
        module.SourcePmModulationConfigureResult(
            1,
            replace(
                modulation,
                kind=Observed.value_of(module.SourceModulationKind.AM),
            ),
            False,
        )


def test_source_v2_burst_write_models_are_closed_and_serializable() -> None:
    burst = module.BurstFacet(
        enabled=Observed.value_of(True),
        mode=Observed.value_of(module.SourceBurstMode.TRIGGERED),
        cycles=Observed.value_of(12),
        phase_deg=Observed.value_of(30.0),
        internal_period_s=Observed.value_of(0.25),
        delay_s=Observed.value_of(0.5),
        gate_polarity=Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        ),
        trigger=Observed.value_of(
            module.SourceTriggerState(
                source=Observed.value_of(module.SourceTriggerSource.INTERNAL),
                slope=Observed.value_of(module.SourceTriggerSlope.POSITIVE),
                output=Observed.value_of(module.SourceTriggerOutput.OFF),
            )
        ),
    )
    request = module.SourceBurstConfigureRequest(
        channel=1,
        cycles=12,
        phase_deg=30.0,
        internal_period_s=0.25,
        delay_s=0.5,
    )
    result = module.SourceBurstConfigureResult(1, burst, False)

    assert module.source_v2_to_data(request) == {
        "type": "SourceBurstConfigureRequest",
        "channel": 1,
        "cycles": 12,
        "phase_deg": 30.0,
        "internal_period_s": 0.25,
        "delay_s": 0.5,
    }
    assert result.burst.mode.value is module.SourceBurstMode.TRIGGERED
    with pytest.raises(ValueError, match="must be <= 500000"):
        module.SourceBurstConfigureRequest(1, 500_001, 30.0, 0.25, 0.5)
    with pytest.raises(ValueError, match="must be > 0"):
        module.SourceBurstConfigureRequest(1, 12, 30.0, 0.0, 0.5)
    with pytest.raises(ValueError, match="output_enabled=False"):
        module.SourceBurstConfigureResult(1, burst, True)
    with pytest.raises(ValueError, match="internal trigger readback"):
        module.SourceBurstConfigureResult(
            1,
            replace(
                burst,
                trigger=Observed.value_of(
                    module.SourceTriggerState(
                        source=Observed.value_of(module.SourceTriggerSource.EXTERNAL),
                        slope=Observed.value_of(module.SourceTriggerSlope.POSITIVE),
                        output=Observed.value_of(module.SourceTriggerOutput.OFF),
                    )
                ),
            ),
            False,
        )


def test_source_v2_fm_modulation_write_models_are_closed_and_serializable() -> None:
    modulation = module.ModulationFacet(
        enabled=Observed.value_of(True),
        kind=Observed.value_of(module.SourceModulationKind.FM),
        source=Observed.value_of(module.SourceModulationSource.INTERNAL),
        parameters=Observed.value_of(
            (
                module.SourceModulationParameter(
                    module.SourceModulationParameterKind.FREQUENCY_DEVIATION_HZ,
                    12_500.0,
                ),
            )
        ),
        internal_frequency_hz=Observed.value_of(25.0),
        internal_waveform_kind=Observed.value_of(module.SourceWaveformKind.SINE),
    )
    request = module.SourceFmModulationConfigureRequest(
        channel=1,
        frequency_deviation_hz=12_500.0,
        internal_frequency_hz=25.0,
    )
    result = module.SourceFmModulationConfigureResult(1, modulation, False)

    assert module.source_v2_to_data(request) == {
        "type": "SourceFmModulationConfigureRequest",
        "channel": 1,
        "frequency_deviation_hz": 12_500.0,
        "internal_frequency_hz": 25.0,
    }
    assert result.modulation.kind.value is module.SourceModulationKind.FM
    with pytest.raises(ValueError, match="must be > 0"):
        module.SourceFmModulationConfigureRequest(1, 0.0, 25.0)
    with pytest.raises(ValueError, match="must be > 0"):
        module.SourceFmModulationConfigureRequest(1, 12_500.0, 0.0)
    with pytest.raises(ValueError, match="output_enabled=False"):
        module.SourceFmModulationConfigureResult(1, modulation, True)
    with pytest.raises(ValueError, match="FM readback"):
        module.SourceFmModulationConfigureResult(
            1,
            replace(
                modulation,
                kind=Observed.value_of(module.SourceModulationKind.PM),
            ),
            False,
        )


def test_source_v2_pwm_modulation_write_models_are_closed_and_serializable() -> None:
    duty_modulation = module.ModulationFacet(
        enabled=Observed.value_of(True),
        kind=Observed.value_of(module.SourceModulationKind.PWM),
        source=Observed.value_of(module.SourceModulationSource.INTERNAL),
        parameters=Observed.value_of(
            (
                module.SourceModulationParameter(
                    module.SourceModulationParameterKind.DUTY_DEVIATION_PERCENT,
                    25.0,
                ),
            )
        ),
        internal_frequency_hz=Observed.value_of(25.0),
        internal_waveform_kind=Observed.value_of(module.SourceWaveformKind.SINE),
    )
    duty_request = module.SourcePwmModulationConfigureRequest(
        channel=1,
        internal_frequency_hz=25.0,
        duty_deviation_percent=25.0,
    )
    duty_result = module.SourcePwmModulationConfigureResult(1, duty_modulation, False)
    width_modulation = replace(
        duty_modulation,
        parameters=Observed.value_of(
            (
                module.SourceModulationParameter(
                    module.SourceModulationParameterKind.WIDTH_DEVIATION_S,
                    1.0e-6,
                ),
            )
        ),
    )
    width_request = module.SourcePwmModulationConfigureRequest(
        channel=1,
        internal_frequency_hz=25.0,
        width_deviation_s=1.0e-6,
    )

    assert module.source_v2_to_data(duty_request) == {
        "type": "SourcePwmModulationConfigureRequest",
        "channel": 1,
        "internal_frequency_hz": 25.0,
        "duty_deviation_percent": 25.0,
        "width_deviation_s": None,
    }
    assert duty_request.deviation_parameter == module.SourceModulationParameter(
        module.SourceModulationParameterKind.DUTY_DEVIATION_PERCENT,
        25.0,
    )
    assert width_request.deviation_parameter == module.SourceModulationParameter(
        module.SourceModulationParameterKind.WIDTH_DEVIATION_S,
        1.0e-6,
    )
    assert duty_result.modulation.parameters.value == duty_modulation.parameters.value
    assert module.SourcePwmModulationConfigureResult(1, width_modulation, False).modulation == (
        width_modulation
    )
    with pytest.raises(ValueError, match="exactly one deviation branch"):
        module.SourcePwmModulationConfigureRequest(channel=1, internal_frequency_hz=25.0)
    with pytest.raises(ValueError, match="exactly one deviation branch"):
        module.SourcePwmModulationConfigureRequest(
            channel=1,
            internal_frequency_hz=25.0,
            duty_deviation_percent=25.0,
            width_deviation_s=1.0e-6,
        )
    with pytest.raises(ValueError, match="must be <= 50.0"):
        module.SourcePwmModulationConfigureRequest(
            channel=1,
            internal_frequency_hz=25.0,
            duty_deviation_percent=50.1,
        )
    with pytest.raises(ValueError, match="PWM readback"):
        module.SourcePwmModulationConfigureResult(
            1,
            replace(duty_modulation, kind=Observed.value_of(module.SourceModulationKind.PM)),
            False,
        )


def test_source_v2_sweep_write_models_are_closed_and_serializable() -> None:
    basic = replace(
        basic_facet(),
        frequency_mode=Observed.value_of(module.SourceFrequencyMode.SWEEP),
    )
    sweep = module.SweepFacet(
        enabled=Observed.value_of(True),
        start_hz=Observed.value_of(100.0),
        stop_hz=Observed.value_of(1_000.0),
        spacing=Observed.value_of(module.SourceSweepSpacing.LINEAR),
        steps=Observed.value_of(101),
        sweep_time_s=Observed.value_of(1.0),
        start_hold_s=Observed.value_of(0.0),
        stop_hold_s=Observed.value_of(0.0),
        return_time_s=Observed.value_of(0.0),
        trigger=Observed.value_of(
            module.SourceTriggerState(
                source=Observed.value_of(module.SourceTriggerSource.INTERNAL),
                slope=Observed.value_of(module.SourceTriggerSlope.POSITIVE),
                output=Observed.value_of(module.SourceTriggerOutput.OFF),
            )
        ),
        marker=Observed.value_of(
            module.SourceSweepMarker(
                enabled=Observed.value_of(False),
                frequency_hz=Observed.missing(
                    Availability.NOT_APPLICABLE,
                    SourceReasonCode.INACTIVE_BY_ANCHOR,
                ),
            )
        ),
    )
    request = module.SourceSweepConfigureRequest(
        channel=1,
        start_hz=100.0,
        stop_hz=1_000.0,
        spacing=module.SourceSweepSpacing.LINEAR,
        steps=101,
        sweep_time_s=1.0,
    )
    result = module.SourceSweepConfigureResult(1, basic, sweep, False)

    assert module.source_v2_to_data(request) == {
        "type": "SourceSweepConfigureRequest",
        "channel": 1,
        "start_hz": 100.0,
        "stop_hz": 1_000.0,
        "spacing": "linear",
        "steps": 101,
        "sweep_time_s": 1.0,
    }
    assert result.sweep.spacing.value is module.SourceSweepSpacing.LINEAR
    with pytest.raises(ValueError, match="start_hz must be > 0"):
        module.SourceSweepConfigureRequest(
            1,
            0.0,
            1_000.0,
            module.SourceSweepSpacing.LINEAR,
            101,
            1.0,
        )
    with pytest.raises(ValueError, match="must not exceed"):
        module.SourceSweepConfigureRequest(
            1,
            1_000.0,
            100.0,
            module.SourceSweepSpacing.LINEAR,
            101,
            1.0,
        )
    with pytest.raises(ValueError, match="<= 2048"):
        module.SourceSweepConfigureRequest(
            1,
            100.0,
            1_000.0,
            module.SourceSweepSpacing.LINEAR,
            2_049,
            1.0,
        )
    with pytest.raises(ValueError, match="output_enabled=False"):
        module.SourceSweepConfigureResult(1, basic, sweep, True)
    with pytest.raises(ValueError, match="sweep frequency mode"):
        module.SourceSweepConfigureResult(
            1,
            replace(
                basic,
                frequency_mode=Observed.value_of(module.SourceFrequencyMode.FIXED),
            ),
            sweep,
            False,
        )
    with pytest.raises(ValueError, match="marker OFF"):
        module.SourceSweepConfigureResult(
            1,
            basic,
            replace(
                sweep,
                marker=Observed.value_of(
                    module.SourceSweepMarker(
                        enabled=Observed.value_of(True),
                        frequency_hz=Observed.value_of(500.0),
                    )
                ),
            ),
            False,
        )


def test_source_v2_pulse_write_models_are_closed_and_serializable() -> None:
    pulse = module.PulseFacet(
        hold_basis=Observed.value_of(module.SourcePulseHoldBasis.WIDTH),
        width_s=Observed.value_of(1.0e-6),
        duty_cycle_percent=Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        ),
        delay_s=Observed.value_of(0.0),
        leading_transition_s=Observed.value_of(1.0e-8),
        trailing_transition_s=Observed.value_of(1.0e-8),
    )
    request = module.SourcePulseConfigureRequest(
        channel=1,
        width_s=1.0e-6,
        delay_s=0.0,
        leading_transition_s=1.0e-8,
        trailing_transition_s=1.0e-8,
    )
    result = module.SourcePulseConfigureResult(1, pulse, False)

    assert module.source_v2_to_data(request) == {
        "type": "SourcePulseConfigureRequest",
        "channel": 1,
        "width_s": 1.0e-6,
        "delay_s": 0.0,
        "leading_transition_s": 1.0e-8,
        "trailing_transition_s": 1.0e-8,
    }
    assert result.pulse.hold_basis.value is module.SourcePulseHoldBasis.WIDTH
    with pytest.raises(ValueError, match="must be >="):
        module.SourcePulseConfigureRequest(1, 3.0e-9, 0.0, 1.0e-9, 1.0e-9)
    with pytest.raises(ValueError, match="must be > 0"):
        module.SourcePulseConfigureRequest(1, 1.0e-6, 0.0, 0.0, 1.0e-8)
    with pytest.raises(ValueError, match="WIDTH hold readback"):
        module.SourcePulseConfigureResult(
            1,
            replace(pulse, hold_basis=Observed.value_of(module.SourcePulseHoldBasis.DUTY)),
            False,
        )
    with pytest.raises(ValueError, match="output_enabled=False"):
        module.SourcePulseConfigureResult(1, pulse, True)


def test_source_v2_arbitrary_write_models_keep_payload_out_of_public_data() -> None:
    digest = "sha256:" + "a" * 64
    previous_digest = "sha256:" + "b" * 64
    storage_request = module.SourceArbitraryStorageRequest(
        channel=1,
        slot_id="slot_a",
        write_mode=module.SourceStorageWriteMode.CREATE_ONLY,
        payload_sha256=digest,
        payload_size_bytes=3,
    )
    assert module.source_v2_to_data(storage_request) == {
        "type": "SourceArbitraryStorageRequest",
        "channel": 1,
        "slot_id": "slot_a",
        "write_mode": "create_only",
        "payload_sha256": digest,
        "payload_size_bytes": 3,
        "expected_previous_sha256": None,
    }
    assert b"abc" not in module.source_v2_canonical_json(storage_request).encode()
    with pytest.raises(ValueError, match="cannot set expected_previous_sha256"):
        module.SourceArbitraryStorageRequest(
            1,
            "slot_a",
            module.SourceStorageWriteMode.CREATE_ONLY,
            digest,
            3,
            previous_digest,
        )
    replace_request = module.SourceArbitraryStorageRequest(
        1,
        "slot_a",
        module.SourceStorageWriteMode.REPLACE_IF_DIGEST_MATCHES,
        digest,
        3,
        previous_digest,
    )
    assert replace_request.expected_previous_sha256 == previous_digest
    with pytest.raises(ValueError, match="require expected_previous_sha256"):
        module.SourceArbitraryStorageRequest(
            1,
            "slot_a",
            module.SourceStorageWriteMode.REPLACE_IF_DIGEST_MATCHES,
            digest,
            3,
        )

    empty_slot = module.SourceArbitraryStorageSlot(1, "slot_a", False)
    assert empty_slot.payload_sha256 is None
    with pytest.raises(ValueError, match="cannot carry payload metadata"):
        module.SourceArbitraryStorageSlot(1, "slot_a", False, digest, 3)
    result = module.SourceArbitraryStorageResult(1, "slot_a", digest, 3, True, False, True)
    assert result.readback_verified is True
    with pytest.raises(ValueError, match="write_completed=True"):
        module.SourceArbitraryStorageResult(1, "slot_a", digest, 3, False, False, True)

    dds = module.SourceArbitrarySelectRequest(
        1,
        "slot_a",
        module.SourceArbitraryPlaybackMode.DDS,
        playback_frequency_hz=1_000.0,
    )
    assert dds.sample_rate_hz is None
    true_arb = module.SourceArbitrarySelectRequest(
        1,
        "slot_a",
        module.SourceArbitraryPlaybackMode.TRUE_ARB,
        sample_rate_hz=10_000.0,
    )
    assert true_arb.playback_frequency_hz is None
    with pytest.raises(ValueError, match="cannot set sample_rate_hz"):
        module.SourceArbitrarySelectRequest(
            1,
            "slot_a",
            module.SourceArbitraryPlaybackMode.DDS,
            playback_frequency_hz=1_000.0,
            sample_rate_hz=10_000.0,
        )
    with pytest.raises(ValueError, match="cannot set playback_frequency_hz"):
        module.SourceArbitrarySelectRequest(
            1,
            "slot_a",
            module.SourceArbitraryPlaybackMode.TRUE_ARB,
            playback_frequency_hz=1_000.0,
            sample_rate_hz=10_000.0,
        )

    basic = replace(
        basic_facet(),
        waveform_kind=Observed.value_of(module.SourceWaveformKind.ARBITRARY),
    )
    arbitrary = module.ArbitraryFacet(
        selected_waveform_id=Observed.value_of("slot_a"),
        playback_mode=Observed.value_of(module.SourceArbitraryPlaybackMode.DDS),
        playback_frequency_hz=Observed.value_of(1_000.0),
        sample_rate_hz=Observed.value_of(10_000.0),
        point_count=Observed.value_of(3),
        storage_digest=Observed.value_of(digest),
    )
    selected = module.SourceArbitrarySelectResult(1, basic, arbitrary, False)
    assert selected.arbitrary.selected_waveform_id.value == "slot_a"
    with pytest.raises(ValueError, match="output_enabled=False"):
        module.SourceArbitrarySelectResult(1, basic, arbitrary, True)


def test_source_v2_cross_channel_write_models_are_closed_and_serializable() -> None:
    relation = module.SourceRelationState(
        feature=module.SourceFeature.COMBINE,
        channels=(1, 2),
        enabled=Observed.value_of(True),
    )
    result = module.SourceCrossChannelConfigureResult(
        feature=module.SourceFeature.COMBINE,
        channels=(1, 2),
        enabled=True,
        relation=relation,
        outputs=(
            module.SourceRelationOutputState(channel=1, enabled=False),
            module.SourceRelationOutputState(channel=2, enabled=False),
        ),
    )
    request_types = (
        module.SourceCombineConfigureRequest,
        module.SourceCouplingConfigureRequest,
        module.SourceTrackingConfigureRequest,
        module.SourcePhaseRelationConfigureRequest,
    )
    contracts = (
        module.SOURCE_COMBINE_CONFIGURE_V2_OPERATION_CONTRACT,
        module.SOURCE_COUPLING_CONFIGURE_V2_OPERATION_CONTRACT,
        module.SOURCE_TRACKING_CONFIGURE_V2_OPERATION_CONTRACT,
        module.SOURCE_PHASE_RELATION_CONFIGURE_V2_OPERATION_CONTRACT,
    )

    for request_type in request_types:
        request = request_type(channels=(1, 2), enabled=True)
        assert module.source_v2_to_data(request) == {
            "type": request_type.__name__,
            "channels": [1, 2],
            "enabled": True,
        }
        with pytest.raises(ValueError, match="two or more channels"):
            request_type(channels=(1,), enabled=True)
        with pytest.raises(ValueError, match="sorted and unique"):
            request_type(channels=(2, 1), enabled=True)

    assert all(contract.recovery_max_steps == 8 for contract in contracts)

    assert result.relation.enabled.value is True
    with pytest.raises(ValueError, match="requires enabled=False"):
        module.SourceRelationOutputState(channel=1, enabled=True)
    with pytest.raises(ValueError, match="does not match request state"):
        module.SourceCrossChannelConfigureResult(
            feature=module.SourceFeature.COMBINE,
            channels=(1, 2),
            enabled=False,
            relation=relation,
            outputs=result.outputs,
        )
    with pytest.raises(ValueError, match="outputs must be sorted and unique"):
        module.SourceCrossChannelConfigureResult(
            feature=module.SourceFeature.COMBINE,
            channels=(1, 2),
            enabled=True,
            relation=relation,
            outputs=(
                module.SourceRelationOutputState(channel=2, enabled=False),
                module.SourceRelationOutputState(channel=1, enabled=False),
            ),
        )


def test_source_v2_coupling_read_model_separates_dimensions_and_parameters() -> None:
    profile = module.SourceCouplingCapabilityProfile(
        dimensions=(
            module.SourceCouplingDimension.AMPLITUDE,
            module.SourceCouplingDimension.FREQUENCY,
            module.SourceCouplingDimension.PHASE,
        ),
        parameter_kinds=(
            module.SourceCouplingParameterKind.AMPLITUDE_DEVIATION_VPP,
            module.SourceCouplingParameterKind.AMPLITUDE_RATIO,
            module.SourceCouplingParameterKind.FREQUENCY_DEVIATION_HZ,
            module.SourceCouplingParameterKind.FREQUENCY_RATIO,
            module.SourceCouplingParameterKind.PHASE_DEVIATION_DEG,
            module.SourceCouplingParameterKind.PHASE_RATIO,
        ),
        supported_channel_sets=((1, 2),),
        global_state_readable=True,
        reference_channel_readable=True,
        relation_graph_readable=False,
    )
    missing = Observed.missing(
        Availability.NOT_QUERIED,
        SourceReasonCode.NOT_REQUESTED,
    )
    state = module.SourceCouplingState(
        feature=module.SourceFeature.COUPLING,
        channels=(1, 2),
        enabled=missing,
        reference_channel=Observed.value_of(1),
        dimensions=(
            module.SourceCouplingDimensionState(
                module.SourceCouplingDimension.AMPLITUDE,
                Observed.value_of(True),
                Observed.value_of(
                    module.SourceCouplingParameter(
                        module.SourceCouplingParameterKind.AMPLITUDE_RATIO,
                        0.5,
                    )
                ),
            ),
            module.SourceCouplingDimensionState(
                module.SourceCouplingDimension.FREQUENCY,
                Observed.value_of(True),
                Observed.value_of(
                    module.SourceCouplingParameter(
                        module.SourceCouplingParameterKind.FREQUENCY_DEVIATION_HZ,
                        500.0,
                    )
                ),
            ),
            module.SourceCouplingDimensionState(
                module.SourceCouplingDimension.PHASE,
                Observed.value_of(False),
                missing,
            ),
        ),
    )

    assert profile.configuration_readable is False
    assert module.source_v2_to_data(state)["dimensions"][0]["parameter"]["value"] == {
        "type": "SourceCouplingParameter",
        "kind": "amplitude_ratio",
        "value": 0.5,
    }
    base = source_extensions()
    mismatched_feature = module.SourceFeatureCapability(
        feature=module.SourceFeature.COUPLING,
        support=module.SupportState.SUPPORTED,
        directions=(module.SourceFeatureDirection.READ,),
        scope=module.SourceFacetScope.CHANNEL_SET,
        channels=(1, 2),
        applicability=module.SourceConstraintApplicability(),
        profile=replace(profile, supported_channel_sets=((1, 3),)),
    )
    with pytest.raises(ValueError, match="channel set is not declared"):
        replace(
            base,
            topology=module.SourceTopologyContract((1, 2, 3)),
            features=(base.features[0], mismatched_feature, base.features[1]),
        )
    with pytest.raises(ValueError, match="does not match its dimension"):
        module.SourceCouplingDimensionState(
            module.SourceCouplingDimension.PHASE,
            Observed.value_of(True),
            Observed.value_of(
                module.SourceCouplingParameter(
                    module.SourceCouplingParameterKind.FREQUENCY_RATIO,
                    2.0,
                )
            ),
        )
    with pytest.raises(ValueError, match="must be a participant"):
        replace(state, reference_channel=Observed.value_of(3))
    with pytest.raises(ValueError, match="feature is not a relation"):
        module.SourceRelationState(
            feature=module.SourceFeature.COUPLING,
            channels=(1, 2),
            enabled=Observed.value_of(True),
        )


def test_source_v2_sync_is_channel_scoped_and_uses_a_dedicated_profile() -> None:
    profile = module.SourceSyncCapabilityProfile(
        enabled_readable=True,
        polarity_readable=True,
        source_channel_readable=True,
        source_channels=(1, 2),
    )
    feature = module.SourceFeatureCapability(
        feature=module.SourceFeature.SYNC,
        support=module.SupportState.SUPPORTED,
        directions=(module.SourceFeatureDirection.READ,),
        scope=module.SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=module.SourceConstraintApplicability(),
        profile=profile,
    )

    assert feature.profile is profile
    assert not hasattr(module, "SourceClockSyncCapabilityProfile")
    with pytest.raises(ValueError, match="cannot use scope"):
        module.SourceFieldRef(
            module.SourceFieldId.SYNC,
            module.SourceScopeRef(module.SourceFacetScope.INSTRUMENT),
        )
    with pytest.raises(ValueError, match="cannot use scope"):
        replace(feature, scope=module.SourceFacetScope.INSTRUMENT, channels=())
    with pytest.raises(ValueError, match="must not be empty"):
        module.SourceSyncCapabilityProfile(True, False, True)
    with pytest.raises(ValueError, match="polarity value has an invalid type"):
        module.SourceSyncState(
            enabled=Observed.value_of(False),
            polarity=Observed.value_of(module.SourceOutputPolarity.NORMAL),
            source_channel=Observed.value_of(1),
        )


def test_source_v2_noise_overlay_is_distinct_from_the_noise_waveform() -> None:
    profile = module.SourceNoiseOverlayCapabilityProfile(
        enabled_readable=True,
        scale_kinds=(
            module.SourceNoiseOverlayScaleKind.PERCENT,
            module.SourceNoiseOverlayScaleKind.RATIO,
            module.SourceNoiseOverlayScaleKind.RATIO_DB,
        ),
    )
    facet = module.NoiseOverlayFacet(
        enabled=Observed.value_of(True),
        scales=Observed.value_of(
            (
                module.SourceNoiseOverlayScale(
                    module.SourceNoiseOverlayScaleKind.PERCENT,
                    25.0,
                ),
                module.SourceNoiseOverlayScale(
                    module.SourceNoiseOverlayScaleKind.RATIO,
                    0.25,
                ),
                module.SourceNoiseOverlayScale(
                    module.SourceNoiseOverlayScaleKind.RATIO_DB,
                    -12.0412,
                ),
            )
        ),
    )

    assert profile.scale_kinds[-1] is module.SourceNoiseOverlayScaleKind.RATIO_DB
    assert module.source_v2_to_data(facet)["scales"]["value"][0] == {
        "type": "SourceNoiseOverlayScale",
        "kind": "percent",
        "value": 25.0,
    }
    assert module.SourceFeature.NOISE_OVERLAY is not module.SourceFeature.BASIC
    assert module.SourceFieldId.NOISE_OVERLAY.value == "source.channel.noise_overlay"
    assert module.SourceNoiseOverlayScale(
        module.SourceNoiseOverlayScaleKind.PERCENT,
        100.0,
    ).value == 100.0
    with pytest.raises(ValueError, match="must be >= 0"):
        module.SourceNoiseOverlayScale(
            module.SourceNoiseOverlayScaleKind.RATIO,
            -0.1,
        )
    with pytest.raises(ValueError, match="must be <= 100"):
        module.SourceNoiseOverlayScale(
            module.SourceNoiseOverlayScaleKind.PERCENT,
            100.1,
        )
    with pytest.raises(ValueError, match="sorted and unique"):
        replace(
            facet,
            scales=Observed.value_of(tuple(reversed(facet.scales.value))),
        )


def test_source_v2_arbitrary_write_capabilities_require_explicit_readback() -> None:
    extensions = source_extensions()
    basic, output = extensions.features
    arbitrary = module.SourceFeatureCapability(
        feature=module.SourceFeature.ARBITRARY,
        support=module.SupportState.SUPPORTED,
        directions=(module.SourceFeatureDirection.CONFIGURE, module.SourceFeatureDirection.READ),
        scope=module.SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=module.SourceConstraintApplicability(),
        profile=module.SourceArbitraryCapabilityProfile(
            playback_modes=(
                module.SourceArbitraryPlaybackMode.DDS,
                module.SourceArbitraryPlaybackMode.TRUE_ARB,
            ),
            selection_readable=True,
            storage_metadata_readable=True,
            sample_rate_readable=True,
            storage_slot_metadata_readable=True,
            storage_write_modes=(
                module.SourceStorageWriteMode.CREATE_ONLY,
                module.SourceStorageWriteMode.REPLACE_IF_DIGEST_MATCHES,
            ),
            storage_max_payload_bytes=4096,
        ),
    )
    arbitrary_query = module.SourceFacetQueryContract(
        feature=module.SourceFeature.ARBITRARY,
        scope=module.SourceFacetScope.CHANNEL,
        fields=(module.SourceFieldId.ARBITRARY_SELECTION,),
        activation_any=(),
        effect=module.SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    configured_extensions = replace(
        extensions,
        features=(
            arbitrary,
            replace(
                basic,
                profile=replace(
                    basic.profile,
                    waveform_kinds=(
                        module.SourceWaveformKind.ARBITRARY,
                        module.SourceWaveformKind.SINE,
                    ),
                ),
            ),
            output,
        ),
        query_contract=replace(
            extensions.query_contract,
            facets=(arbitrary_query, *extensions.query_contract.facets),
            max_queries=7,
        ),
    )
    descriptor = replace(
        source_descriptor(extensions=configured_extensions),
        capabilities=(
            "source.snapshot_v2",
            "source.arbitrary_storage_v2",
            "source.arbitrary_select_v2",
        ),
    )

    class ArbitraryWriteDriver(SourceV2FakeDriver):
        def read_source_arbitrary_storage_v2(self, channel, slot_id):
            raise AssertionError((channel, slot_id))

        def mutate_source_arbitrary_storage_v2(self, request, payload):
            raise AssertionError((request, payload))

        def select_source_arbitrary_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, ArbitraryWriteDriver(combined=True))

    with pytest.raises(ConfigError, match="authoritative slot metadata"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        replace(
                            arbitrary,
                            profile=replace(
                                arbitrary.profile,
                                storage_slot_metadata_readable=False,
                            ),
                        ),
                        configured_extensions.features[1],
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="arbitrary basic waveform"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(arbitrary, basic, output),
                ),
            )
        )
    with pytest.raises(TypeError, match="mutate_source_arbitrary_storage_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingArbitraryStorageDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
                    "read_source_arbitrary_storage_v2": lambda self, channel, slot_id: None,
                    "select_source_arbitrary_v2": lambda self, request: None,
                },
            )(),
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


def test_source_output_v2_descriptor_keeps_off_available_without_final_vpp_readback() -> None:
    extensions = source_extensions()
    basic, output = extensions.features
    output_only_extensions = replace(
        extensions,
        features=(
            replace(
                basic,
                profile=replace(
                    basic.profile,
                    amplitude_units=(module.SourceAmplitudeUnit.VRMS,),
                    offset_readable=False,
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
        source_descriptor(extensions=output_only_extensions),
        capabilities=("source.snapshot_v2", "source.output_v2"),
    )

    validate_source_descriptor(descriptor)


def test_source_v2_harmonic_write_requires_direction_and_configuration_readback() -> None:
    extensions = source_extensions_with_harmonics()
    basic, harmonic, output = extensions.features
    configured_harmonic = replace(
        harmonic,
        directions=(SourceFeatureDirection.CONFIGURE, SourceFeatureDirection.READ),
        profile=replace(
            harmonic.profile,
            presets=(
                module.SourceHarmonicPreset.ALL,
                module.SourceHarmonicPreset.EVEN,
                module.SourceHarmonicPreset.ODD,
            ),
            configured_order_readable=True,
            preset_readable=True,
        ),
    )
    configured_extensions = replace(
        extensions,
        features=(basic, configured_harmonic, output),
    )
    descriptor = replace(
        source_descriptor(extensions=configured_extensions),
        capabilities=("source.snapshot_v2", "source.harmonics_configure_v2"),
    )

    class HarmonicWriteDriver(SourceV2FakeDriver):
        def configure_source_harmonics_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, HarmonicWriteDriver(combined=True))

    with pytest.raises(ConfigError, match="CONFIGURE directions"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(basic, harmonic, output),
                ),
            )
        )
    with pytest.raises(ConfigError, match="configured order and preset"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(
                            configured_harmonic,
                            profile=replace(
                                configured_harmonic.profile,
                                preset_readable=False,
                            ),
                        ),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(TypeError, match="configure_source_harmonics_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingHarmonicWriteDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
                },
        )(),
    )


def test_source_v2_harmonic_disable_requires_direction_and_state_readback() -> None:
    extensions = source_extensions_with_harmonics()
    basic, harmonic, output = extensions.features
    disableable_harmonic = replace(
        harmonic,
        directions=(SourceFeatureDirection.DISABLE, SourceFeatureDirection.READ),
    )
    disableable_extensions = replace(
        extensions,
        features=(basic, disableable_harmonic, output),
    )
    descriptor = replace(
        source_descriptor(extensions=disableable_extensions),
        capabilities=("source.snapshot_v2", "source.harmonics_disable_v2"),
    )

    class HarmonicDisableDriver(SourceV2FakeDriver):
        def disable_source_harmonics_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, HarmonicDisableDriver(combined=True))

    with pytest.raises(ConfigError, match="DISABLE directions"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    disableable_extensions,
                    features=(basic, harmonic, output),
                ),
            )
        )
    with pytest.raises(ConfigError, match="must declare read"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    disableable_extensions,
                    features=(
                        basic,
                        replace(
                            disableable_harmonic,
                            directions=(SourceFeatureDirection.DISABLE,),
                        ),
                        output,
                    ),
                    query_contract=replace(
                        disableable_extensions.query_contract,
                        facets=tuple(
                            facet
                            for facet in disableable_extensions.query_contract.facets
                            if facet.feature is not module.SourceFeature.HARMONICS
                        ),
                    ),
                ),
            )
        )
    with pytest.raises(TypeError, match="disable_source_harmonics_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingHarmonicDisableDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
                },
            )(),
        )


def test_source_v2_modulation_write_requires_internal_am_direction_and_readback() -> None:
    extensions = source_extensions()
    basic, output = extensions.features
    modulation = module.SourceFeatureCapability(
        feature=module.SourceFeature.MODULATION,
        support=module.SupportState.SUPPORTED,
        directions=(module.SourceFeatureDirection.CONFIGURE, module.SourceFeatureDirection.READ),
        scope=module.SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=module.SourceConstraintApplicability(),
        profile=module.SourceModulationCapabilityProfile(
            kinds=(module.SourceModulationKind.AM,),
            sources=(module.SourceModulationSource.INTERNAL,),
            parameter_kinds=(module.SourceModulationParameterKind.DEPTH_PERCENT,),
            inactive_readable=False,
            configuration_readable=True,
        ),
    )
    modulation_query = module.SourceFacetQueryContract(
        feature=module.SourceFeature.MODULATION,
        scope=module.SourceFacetScope.CHANNEL,
        fields=(module.SourceFieldId.MODULATION,),
        activation_any=(),
        effect=module.SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    configured_extensions = replace(
        extensions,
        features=(basic, modulation, output),
        query_contract=replace(
            extensions.query_contract,
            facets=(
                extensions.query_contract.facets[0],
                extensions.query_contract.facets[1],
                modulation_query,
                extensions.query_contract.facets[2],
            ),
            max_queries=7,
        ),
    )
    descriptor = replace(
        source_descriptor(extensions=configured_extensions),
        capabilities=("source.snapshot_v2", "source.modulation_configure_v2"),
    )

    class ModulationWriteDriver(SourceV2FakeDriver):
        def configure_source_modulation_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, ModulationWriteDriver(combined=True))

    with pytest.raises(ConfigError, match="CONFIGURE directions"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(modulation, directions=(module.SourceFeatureDirection.READ,)),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable internal AM configuration"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(
                            modulation,
                            profile=replace(
                                modulation.profile,
                                configuration_readable=False,
                            ),
                        ),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable output state"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        modulation,
                        replace(output, profile=replace(output.profile, output_readable=False)),
                    ),
                ),
            )
        )
    with pytest.raises(TypeError, match="configure_source_modulation_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingModulationWriteDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
                },
            )(),
        )


def test_source_v2_pm_modulation_write_requires_internal_pm_direction_and_readback() -> None:
    extensions = source_extensions()
    basic, output = extensions.features
    modulation = module.SourceFeatureCapability(
        feature=module.SourceFeature.MODULATION,
        support=module.SupportState.SUPPORTED,
        directions=(module.SourceFeatureDirection.CONFIGURE, module.SourceFeatureDirection.READ),
        scope=module.SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=module.SourceConstraintApplicability(),
        profile=module.SourceModulationCapabilityProfile(
            kinds=(module.SourceModulationKind.PM,),
            sources=(module.SourceModulationSource.INTERNAL,),
            parameter_kinds=(module.SourceModulationParameterKind.PHASE_DEVIATION_DEG,),
            inactive_readable=False,
            configuration_readable=True,
        ),
    )
    modulation_query = module.SourceFacetQueryContract(
        feature=module.SourceFeature.MODULATION,
        scope=module.SourceFacetScope.CHANNEL,
        fields=(module.SourceFieldId.MODULATION,),
        activation_any=(),
        effect=module.SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    configured_extensions = replace(
        extensions,
        features=(basic, modulation, output),
        query_contract=replace(
            extensions.query_contract,
            facets=(
                extensions.query_contract.facets[0],
                extensions.query_contract.facets[1],
                modulation_query,
                extensions.query_contract.facets[2],
            ),
            max_queries=7,
        ),
    )
    descriptor = replace(
        source_descriptor(extensions=configured_extensions),
        capabilities=("source.snapshot_v2", "source.modulation_pm_configure_v2"),
    )

    class PmModulationWriteDriver(SourceV2FakeDriver):
        def configure_source_pm_modulation_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, PmModulationWriteDriver(combined=True))

    with pytest.raises(ConfigError, match="CONFIGURE directions"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(modulation, directions=(module.SourceFeatureDirection.READ,)),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable internal PM configuration"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(
                            modulation,
                            profile=replace(
                                modulation.profile,
                                configuration_readable=False,
                            ),
                        ),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable output state"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        modulation,
                        replace(output, profile=replace(output.profile, output_readable=False)),
                    ),
                ),
            )
        )
    with pytest.raises(TypeError, match="configure_source_pm_modulation_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingPmModulationWriteDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
                },
            )(),
        )


def test_source_v2_fm_modulation_write_requires_internal_fm_direction_and_readback() -> None:
    extensions = source_extensions()
    basic, output = extensions.features
    modulation = module.SourceFeatureCapability(
        feature=module.SourceFeature.MODULATION,
        support=module.SupportState.SUPPORTED,
        directions=(module.SourceFeatureDirection.CONFIGURE, module.SourceFeatureDirection.READ),
        scope=module.SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=module.SourceConstraintApplicability(),
        profile=module.SourceModulationCapabilityProfile(
            kinds=(module.SourceModulationKind.FM,),
            sources=(module.SourceModulationSource.INTERNAL,),
            parameter_kinds=(
                module.SourceModulationParameterKind.FREQUENCY_DEVIATION_HZ,
            ),
            inactive_readable=False,
            configuration_readable=True,
        ),
    )
    modulation_query = module.SourceFacetQueryContract(
        feature=module.SourceFeature.MODULATION,
        scope=module.SourceFacetScope.CHANNEL,
        fields=(module.SourceFieldId.MODULATION,),
        activation_any=(),
        effect=module.SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    configured_extensions = replace(
        extensions,
        features=(basic, modulation, output),
        query_contract=replace(
            extensions.query_contract,
            facets=(
                extensions.query_contract.facets[0],
                extensions.query_contract.facets[1],
                modulation_query,
                extensions.query_contract.facets[2],
            ),
            max_queries=7,
        ),
    )
    descriptor = replace(
        source_descriptor(extensions=configured_extensions),
        capabilities=("source.snapshot_v2", "source.modulation_fm_configure_v2"),
    )

    class FmModulationWriteDriver(SourceV2FakeDriver):
        def configure_source_fm_modulation_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, FmModulationWriteDriver(combined=True))

    with pytest.raises(ConfigError, match="CONFIGURE directions"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(modulation, directions=(module.SourceFeatureDirection.READ,)),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable internal FM configuration"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(
                            modulation,
                            profile=replace(
                                modulation.profile,
                                configuration_readable=False,
                            ),
                        ),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable output state"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        modulation,
                        replace(output, profile=replace(output.profile, output_readable=False)),
                    ),
                ),
            )
        )
    with pytest.raises(TypeError, match="configure_source_fm_modulation_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingFmModulationWriteDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
                },
            )(),
        )


def test_source_v2_pwm_modulation_write_requires_internal_pwm_direction_and_readback() -> None:
    extensions = source_extensions()
    basic, output = extensions.features
    modulation = module.SourceFeatureCapability(
        feature=module.SourceFeature.MODULATION,
        support=module.SupportState.SUPPORTED,
        directions=(module.SourceFeatureDirection.CONFIGURE, module.SourceFeatureDirection.READ),
        scope=module.SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=module.SourceConstraintApplicability(),
        profile=module.SourceModulationCapabilityProfile(
            kinds=(module.SourceModulationKind.PWM,),
            sources=(module.SourceModulationSource.INTERNAL,),
            parameter_kinds=(
                module.SourceModulationParameterKind.DUTY_DEVIATION_PERCENT,
            ),
            inactive_readable=False,
            configuration_readable=True,
        ),
    )
    modulation_query = module.SourceFacetQueryContract(
        feature=module.SourceFeature.MODULATION,
        scope=module.SourceFacetScope.CHANNEL,
        fields=(module.SourceFieldId.MODULATION,),
        activation_any=(),
        effect=module.SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    configured_extensions = replace(
        extensions,
        features=(basic, modulation, output),
        query_contract=replace(
            extensions.query_contract,
            facets=(
                extensions.query_contract.facets[0],
                extensions.query_contract.facets[1],
                modulation_query,
                extensions.query_contract.facets[2],
            ),
            max_queries=7,
        ),
    )
    descriptor = replace(
        source_descriptor(extensions=configured_extensions),
        capabilities=("source.snapshot_v2", "source.modulation_pwm_configure_v2"),
    )

    class PwmModulationWriteDriver(SourceV2FakeDriver):
        def configure_source_pwm_modulation_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, PwmModulationWriteDriver(combined=True))

    with pytest.raises(ConfigError, match="CONFIGURE directions"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(modulation, directions=(module.SourceFeatureDirection.READ,)),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable internal PWM configuration"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(
                            modulation,
                            profile=replace(
                                modulation.profile,
                                parameter_kinds=(
                                    module.SourceModulationParameterKind.DEPTH_PERCENT,
                                ),
                            ),
                        ),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable output state"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        modulation,
                        replace(output, profile=replace(output.profile, output_readable=False)),
                    ),
                ),
            )
        )
    with pytest.raises(TypeError, match="configure_source_pwm_modulation_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingPwmModulationWriteDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
                },
            )(),
        )


def test_source_v2_pulse_write_requires_width_direction_and_readback() -> None:
    extensions = source_extensions()
    basic, output = extensions.features
    pulse = module.SourceFeatureCapability(
        feature=module.SourceFeature.PULSE,
        support=module.SupportState.SUPPORTED,
        directions=(module.SourceFeatureDirection.CONFIGURE, module.SourceFeatureDirection.READ),
        scope=module.SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=module.SourceConstraintApplicability(),
        profile=module.SourcePulseCapabilityProfile(
            hold_modes=(module.SourcePulseHoldBasis.WIDTH,),
            delay_readable=True,
            transitions_readable=True,
            width_configuration_readable=True,
        ),
    )
    pulse_query = module.SourceFacetQueryContract(
        feature=module.SourceFeature.PULSE,
        scope=module.SourceFacetScope.CHANNEL,
        fields=(module.SourceFieldId.PULSE,),
        activation_any=(),
        effect=module.SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    configured_extensions = replace(
        extensions,
        features=(basic, output, pulse),
        query_contract=replace(
            extensions.query_contract,
            facets=(
                extensions.query_contract.facets[0],
                extensions.query_contract.facets[1],
                extensions.query_contract.facets[2],
                pulse_query,
            ),
            max_queries=7,
        ),
    )
    descriptor = replace(
        source_descriptor(extensions=configured_extensions),
        capabilities=("source.snapshot_v2", "source.pulse_configure_v2"),
    )

    class PulseWriteDriver(SourceV2FakeDriver):
        def configure_source_pulse_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, PulseWriteDriver(combined=True))

    with pytest.raises(ConfigError, match="CONFIGURE directions"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        output,
                        replace(pulse, directions=(module.SourceFeatureDirection.READ,)),
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable WIDTH pulse configuration"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        output,
                        replace(
                            pulse,
                            profile=replace(
                                pulse.profile,
                                width_configuration_readable=False,
                            ),
                        ),
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable output state"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(output, profile=replace(output.profile, output_readable=False)),
                        pulse,
                    ),
                ),
            )
        )
    with pytest.raises(TypeError, match="configure_source_pulse_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingPulseWriteDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
                },
            )(),
        )


def test_source_v2_burst_write_requires_triggered_internal_direction_and_readback() -> None:
    extensions = source_extensions()
    basic, output = extensions.features
    burst = module.SourceFeatureCapability(
        feature=module.SourceFeature.BURST,
        support=module.SupportState.SUPPORTED,
        directions=(module.SourceFeatureDirection.CONFIGURE, module.SourceFeatureDirection.READ),
        scope=module.SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=module.SourceConstraintApplicability(),
        profile=module.SourceBurstCapabilityProfile(
            modes=(module.SourceBurstMode.TRIGGERED,),
            trigger_sources=(module.SourceTriggerSource.INTERNAL,),
            timing_readable=True,
            gate_readable=False,
            triggered_internal_configuration_readable=True,
        ),
    )
    burst_query = module.SourceFacetQueryContract(
        feature=module.SourceFeature.BURST,
        scope=module.SourceFacetScope.CHANNEL,
        fields=(module.SourceFieldId.BURST,),
        activation_any=(),
        effect=module.SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    configured_extensions = replace(
        extensions,
        features=(basic, burst, output),
        query_contract=replace(
            extensions.query_contract,
            facets=(
                extensions.query_contract.facets[0],
                extensions.query_contract.facets[1],
                burst_query,
                extensions.query_contract.facets[2],
            ),
            max_queries=7,
        ),
    )
    descriptor = replace(
        source_descriptor(extensions=configured_extensions),
        capabilities=("source.snapshot_v2", "source.burst_configure_v2"),
    )

    class BurstWriteDriver(SourceV2FakeDriver):
        def configure_source_burst_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, BurstWriteDriver(combined=True))

    with pytest.raises(ConfigError, match="CONFIGURE directions"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(burst, directions=(module.SourceFeatureDirection.READ,)),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable internal triggered burst configuration"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(
                            burst,
                            profile=replace(
                                burst.profile,
                                triggered_internal_configuration_readable=False,
                            ),
                        ),
                        output,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable output state"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        burst,
                        replace(output, profile=replace(output.profile, output_readable=False)),
                    ),
                ),
            )
        )
    with pytest.raises(TypeError, match="configure_source_burst_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingBurstWriteDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
                },
            )(),
        )


def test_source_v2_sweep_write_requires_internal_direction_and_readback() -> None:
    extensions = source_extensions()
    basic, output = extensions.features
    basic = replace(
        basic,
        profile=replace(
            basic.profile,
            frequency_modes=(
                module.SourceFrequencyMode.FIXED,
                module.SourceFrequencyMode.SWEEP,
            ),
        ),
    )
    sweep = module.SourceFeatureCapability(
        feature=module.SourceFeature.SWEEP,
        support=module.SupportState.SUPPORTED,
        directions=(module.SourceFeatureDirection.CONFIGURE, module.SourceFeatureDirection.READ),
        scope=module.SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=module.SourceConstraintApplicability(),
        profile=module.SourceSweepCapabilityProfile(
            spacing_modes=(
                module.SourceSweepSpacing.LINEAR,
                module.SourceSweepSpacing.LOGARITHMIC,
                module.SourceSweepSpacing.STEP,
            ),
            trigger_sources=(module.SourceTriggerSource.INTERNAL,),
            timing_readable=True,
            marker_readable=True,
            configuration_readable=True,
        ),
    )
    sweep_query = module.SourceFacetQueryContract(
        feature=module.SourceFeature.SWEEP,
        scope=module.SourceFacetScope.CHANNEL,
        fields=(module.SourceFieldId.SWEEP,),
        activation_any=(),
        effect=module.SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    configured_extensions = replace(
        extensions,
        features=(basic, output, sweep),
        query_contract=replace(
            extensions.query_contract,
            facets=(
                extensions.query_contract.facets[0],
                extensions.query_contract.facets[1],
                extensions.query_contract.facets[2],
                sweep_query,
            ),
            max_queries=7,
        ),
    )
    descriptor = replace(
        source_descriptor(extensions=configured_extensions),
        capabilities=("source.snapshot_v2", "source.sweep_configure_v2"),
    )

    class SweepWriteDriver(SourceV2FakeDriver):
        def configure_source_sweep_v2(self, request):
            raise AssertionError(request)

    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, SweepWriteDriver(combined=True))

    with pytest.raises(ConfigError, match="CONFIGURE directions"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        output,
                        replace(sweep, directions=(module.SourceFeatureDirection.READ,)),
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable internal sweep configuration"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        output,
                        replace(
                            sweep,
                            profile=replace(
                                sweep.profile,
                                configuration_readable=False,
                            ),
                        ),
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="sweep frequency mode"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        replace(
                            basic,
                            profile=replace(
                                basic.profile,
                                frequency_modes=(module.SourceFrequencyMode.FIXED,),
                            ),
                        ),
                        output,
                        sweep,
                    ),
                ),
            )
        )
    with pytest.raises(ConfigError, match="readable output state"):
        validate_source_descriptor(
            replace(
                descriptor,
                source_extensions=replace(
                    configured_extensions,
                    features=(
                        basic,
                        replace(output, profile=replace(output.profile, output_readable=False)),
                        sweep,
                    ),
                ),
            )
        )
    with pytest.raises(TypeError, match="configure_source_sweep_v2"):
        validate_declared_capabilities(
            descriptor,
            type(
                "MissingSweepWriteDriver",
                (),
                {
                    "close": lambda self: None,
                    "execute_source_query_plan_v2": lambda self, plan: None,
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
