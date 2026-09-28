from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    SafetyLimitsConfig,
    ScopeConfig,
    SourceConfig,
    SourceTerminationConfig,
    WaveBenchConfig,
    WaveformConfig,
    load_config,
)
from wavebench.errors import ConfigError
from wavebench.instruments.source_extensions import (
    ResistanceBounds,
    SourceFacetScope,
    SourceScopeRef,
    SourceTerminationEvidence,
    TerminationEvidenceLifetime,
    TerminationEvidenceSource,
    TerminationKind,
    TerminationSpec,
)
from wavebench.services.source_safety import (
    SourceTerminationEvidenceStatus,
    source_config_termination_evidence,
    source_termination_binding_digest,
    source_termination_evidence_context,
    validate_source_termination_evidence,
)


def make_config() -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "DMAX"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("wavebench.toml"),
        source=SourceConfig(
            "example.source",
            "TCPIP::source::INSTR",
            1,
            True,
            True,
            0,
            terminations=(
                SourceTerminationConfig(1, "resistive", 49.5, 50.5),
                SourceTerminationConfig(2, "high_impedance"),
            ),
        ),
        safety_limits=SafetyLimitsConfig(
            max_source_vpp=2.0,
            min_source_port_voltage_v=-2.0,
            max_source_port_voltage_v=2.0,
        ),
    )


def test_termination_spec_requires_explicit_resistive_bounds() -> None:
    assert TerminationSpec(TerminationKind.HIGH_IMPEDANCE).resistance_bounds is None
    assert TerminationSpec(
        TerminationKind.RESISTIVE,
        ResistanceBounds(49.5, 50.5),
    ).kind is TerminationKind.RESISTIVE

    with pytest.raises(ValueError, match="requires resistance_bounds"):
        TerminationSpec(TerminationKind.RESISTIVE)
    with pytest.raises(ValueError, match="source and lifetime"):
        SourceTerminationEvidence(
            target=SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),
            termination=TerminationSpec(TerminationKind.HIGH_IMPEDANCE),
            source=TerminationEvidenceSource.CONFIG,
            lifetime=TerminationEvidenceLifetime.OPERATION,
            resource_fingerprint="sha256:" + "0" * 64,
            binding_digest="sha256:" + "1" * 64,
            observed_at_utc="2026-08-22T00:00:00.000Z",
            expires_at_utc=None,
            evidence_ref="test.evidence",
        )


def test_config_termination_evidence_is_bound_to_current_operation() -> None:
    config = make_config()
    target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=1)
    context = source_termination_evidence_context(
        config,
        target=target,
        correlation_id="correlation-1",
        observed_at_utc="2026-08-22T00:00:00.000Z",
    )

    evidence = source_config_termination_evidence(config, context=context)

    assert evidence is not None
    assert evidence.termination.kind is TerminationKind.RESISTIVE
    assert evidence.termination.resistance_bounds == ResistanceBounds(49.5, 50.5)
    assert evidence.source is TerminationEvidenceSource.CONFIG
    assert evidence.lifetime is TerminationEvidenceLifetime.CONFIG_DIGEST
    assert validate_source_termination_evidence(evidence, context=context).is_valid


def test_missing_config_termination_is_not_inferred_from_display_state() -> None:
    config = make_config()
    target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=3)
    context = source_termination_evidence_context(
        config,
        target=target,
        correlation_id="correlation-1",
        observed_at_utc="2026-08-22T00:00:00.000Z",
    )

    assert source_config_termination_evidence(config, context=context) is None


@pytest.mark.parametrize(
    ("change", "expected"),
    (
        ("target", SourceTerminationEvidenceStatus.TARGET_MISMATCH),
        ("resource", SourceTerminationEvidenceStatus.RESOURCE_MISMATCH),
        ("binding", SourceTerminationEvidenceStatus.BINDING_MISMATCH),
        ("expired", SourceTerminationEvidenceStatus.EXPIRED),
        ("future", SourceTerminationEvidenceStatus.OBSERVED_IN_FUTURE),
    ),
)
def test_termination_evidence_rejects_mismatched_or_expired_binding(
    change: str,
    expected: SourceTerminationEvidenceStatus,
) -> None:
    config = make_config()
    context = source_termination_evidence_context(
        config,
        target=SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),
        correlation_id="correlation-1",
        observed_at_utc="2026-08-22T12:00:00.000Z",
    )
    evidence = source_config_termination_evidence(config, context=context)
    assert evidence is not None
    if change == "target":
        evidence = replace(
            evidence,
            target=SourceScopeRef(SourceFacetScope.CHANNEL, channel=2),
        )
    elif change == "resource":
        evidence = replace(evidence, resource_fingerprint="sha256:" + "2" * 64)
    elif change == "binding":
        evidence = replace(evidence, binding_digest="sha256:" + "3" * 64)
    elif change == "expired":
        evidence = replace(
            evidence,
            expires_at_utc="2026-08-22T11:00:00.000Z",
            observed_at_utc="2026-08-22T10:00:00.000Z",
        )
    else:
        evidence = replace(evidence, observed_at_utc="2026-08-22T13:00:00.000Z")

    result = validate_source_termination_evidence(evidence, context=context)

    assert result.status is expected
    assert result.evidence is None


def test_run_evidence_requires_run_intent_binding() -> None:
    config = make_config()
    context = source_termination_evidence_context(
        config,
        target=SourceScopeRef(SourceFacetScope.CHANNEL, channel=1),
        correlation_id="correlation-1",
        observed_at_utc="2026-08-22T00:00:00.000Z",
    )
    with pytest.raises(ConfigError, match="run intent digest"):
        source_termination_binding_digest(
            context,
            source=TerminationEvidenceSource.RUN_INTENT,
            lifetime=TerminationEvidenceLifetime.RUN,
        )


def test_source_config_parser_preserves_sorted_termination_entries(tmp_path: Path) -> None:
    path = tmp_path / "wavebench.toml"
    path.write_text(
        """
[connection]
resource = "TCPIP::127.0.0.1::INSTR"

[scope]

[source]
resource = "TCPIP::127.0.0.2::INSTR"

[[source.terminations]]
channel = 2
kind = "high_impedance"

[[source.terminations]]
channel = 1
kind = "resistive"
minimum_ohm = 49.5
maximum_ohm = 50.5
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.source is not None
    assert config.source.terminations == (
        SourceTerminationConfig(1, "resistive", 49.5, 50.5),
        SourceTerminationConfig(2, "high_impedance"),
    )
    assert config.with_source_resource("TCPIP::127.0.0.3::INSTR").source.terminations == (
        SourceTerminationConfig(1, "resistive", 49.5, 50.5),
        SourceTerminationConfig(2, "high_impedance"),
    )


@pytest.mark.parametrize(
    "entry",
    (
        "channel = 1\nkind = \"resistive\"",
        "channel = 1\nkind = \"resistive\"\nminimum_ohm = 50",
        "channel = 1\nkind = \"resistive\"\nminimum_ohm = 50\nmaximum_ohm = 49",
        "channel = true\nkind = \"high_impedance\"",
        "channel = 1\nkind = \"unknown\"",
    ),
)
def test_source_config_parser_rejects_invalid_termination_entries(
    tmp_path: Path,
    entry: str,
) -> None:
    path = tmp_path / "wavebench.toml"
    path.write_text(
        "[connection]\nresource = \"TCPIP::127.0.0.1::INSTR\"\n[scope]\n[source]\n"
        "resource = \"TCPIP::127.0.0.2::INSTR\"\n[[source.terminations]]\n"
        f"{entry}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="source.terminations"):
        load_config(path)
