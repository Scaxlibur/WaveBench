from datetime import datetime, timezone

import numpy as np
import pytest

from wavebench.instruments import (
    FrequencyResponseTrace,
    InstrumentMeasurementResult,
    MarkerReading,
    SweepAnalyzerDriver,
    SweepAnalyzerSnapshot,
    SweepPlan,
    TraceIntegrity,
)
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.capabilities import (
    CAPABILITY_METHODS,
    validate_declared_capabilities,
)
from wavebench.plugins.api import InstrumentPlugin, VALID_PLUGIN_KINDS
from wavebench.plugins.registry import PluginRegistry, plugin_doctor_records


def test_sweep_plan_requires_one_mode_specific_frequency_definition():
    sweep = SweepPlan(
        mode="sweep",
        start_frequency_hz=20.0,
        stop_frequency_hz=120_000_000.0,
        axis="logarithmic",
        sweep_time_s=None,
        acquisition="single",
        trigger="internal",
        averaging_enabled=True,
        average_count=4,
        points=401,
        source_output_enabled=False,
        source_level=-20.0,
        source_level_unit="dbm",
        source_impedance_ohm=50.0,
    )
    cw = SweepPlan(mode="cw", cw_frequency_hz=1_000.0)

    assert sweep.uses_start_stop
    assert not sweep.uses_center_span
    assert sweep.sweep_time_mode == "auto"
    assert cw.cw_frequency_hz == 1_000.0

    with pytest.raises(ValueError, match="exactly one frequency window"):
        SweepPlan(
            mode="sweep",
            start_frequency_hz=20.0,
            stop_frequency_hz=1_000.0,
            center_frequency_hz=500.0,
            span_frequency_hz=100.0,
        )
    with pytest.raises(ValueError, match="cw_frequency_hz"):
        SweepPlan(mode="cw")
    with pytest.raises(ValueError, match="finite"):
        SweepPlan(mode="cw", cw_frequency_hz=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        SweepPlan(
            mode="sweep",
            start_frequency_hz=20.0,
            stop_frequency_hz=1_000.0,
            source_level=float("inf"),
            source_level_unit="dbm",
        )


def test_frequency_response_trace_preserves_provenance_units_and_integrity():
    integrity = TraceIntegrity(
        complete=True,
        expected_points=3,
        actual_points=3,
    )
    trace = FrequencyResponseTrace(
        frequency_hz=np.array([100.0, 200.0, 300.0]),
        magnitude=np.array([-1.0, -2.0, -3.0]),
        phase_deg=np.array([0.0, -10.0, -20.0]),
        magnitude_unit="db",
        magnitude_semantics="relative",
        axis_source="derived",
        integrity=integrity,
        acquired_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        raw_evidence_ref="artifact://trace-001",
    )

    assert trace.point_count == 3
    assert trace.integrity.complete
    assert trace.axis_source == "derived"
    assert trace.magnitude_unit == "db"
    assert trace.magnitude_semantics == "relative"
    assert not trace.frequency_hz.flags.writeable
    assert not trace.magnitude.flags.writeable

    with pytest.raises(ValueError, match="same number of points"):
        FrequencyResponseTrace(
            frequency_hz=np.array([1.0, 2.0]),
            magnitude=np.array([1.0]),
            phase_deg=None,
            magnitude_unit="dbm",
            magnitude_semantics="absolute",
            axis_source="device",
            integrity=TraceIntegrity(
                complete=False,
                expected_points=2,
                actual_points=1,
                warnings=("point count mismatch",),
            ),
            acquired_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError, match="finite"):
        FrequencyResponseTrace(
            frequency_hz=None,
            magnitude=np.array([float("nan")]),
            phase_deg=None,
            magnitude_unit="unknown",
            magnitude_semantics="unknown",
            axis_source="unknown",
            integrity=TraceIntegrity(
                complete=False,
                expected_points=None,
                actual_points=1,
                warnings=("nonfinite token",),
            ),
            acquired_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError, match="magnitude unit and semantics"):
        FrequencyResponseTrace(
            frequency_hz=np.array([1_000.0]),
            magnitude=np.array([-3.0]),
            phase_deg=None,
            magnitude_unit="dbm",
            magnitude_semantics="relative",
            axis_source="device",
            integrity=TraceIntegrity(
                complete=True,
                expected_points=1,
                actual_points=1,
            ),
            acquired_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError, match="positive"):
        FrequencyResponseTrace(
            frequency_hz=np.array([0.0]),
            magnitude=np.array([1.0]),
            phase_deg=None,
            magnitude_unit="dbm",
            magnitude_semantics="absolute",
            axis_source="device",
            integrity=TraceIntegrity(
                complete=True,
                expected_points=1,
                actual_points=1,
            ),
            acquired_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError, match="warnings"):
        TraceIntegrity(complete=False, expected_points=3, actual_points=2)


def test_snapshot_marker_and_measurement_models_keep_vendor_neutral_semantics():
    snapshot = SweepAnalyzerSnapshot(
        effective_plan=SweepPlan(
            mode="sweep",
            center_frequency_hz=1_000_000.0,
            span_frequency_hz=100_000.0,
        ),
        requested_plan=SweepPlan(
            mode="sweep",
            center_frequency_hz=1_000_000.0,
            span_frequency_hz=100_000.0,
        ),
        frequency_offset_hz=0.0,
        magnitude_measurement_enabled=True,
        phase_measurement_enabled=True,
    )
    marker = MarkerReading(
        index=1,
        frequency_hz=1_000_000.0,
        magnitude=-3.0,
        magnitude_unit="db",
        magnitude_semantics="relative",
        phase_deg=-45.0,
        delta_magnitude=-0.5,
        delta_magnitude_unit="db",
    )
    result = InstrumentMeasurementResult(
        name="bandwidth_3db",
        value=125_000.0,
        unit="hz",
        method="instrument",
    )

    assert snapshot.effective_plan.uses_center_span
    assert snapshot.requested_plan is not None
    assert marker.index == 1
    assert marker.delta_magnitude_unit == "db"
    assert result.method == "instrument"

    with pytest.raises(ValueError, match="at least one"):
        SweepAnalyzerSnapshot(
            effective_plan=snapshot.effective_plan,
            magnitude_measurement_enabled=False,
            phase_measurement_enabled=False,
        )
    with pytest.raises(ValueError, match="finite"):
        SweepAnalyzerSnapshot(
            effective_plan=snapshot.effective_plan,
            frequency_offset_hz=float("nan"),
        )

    with pytest.raises(ValueError, match="semantics"):
        MarkerReading(
            index=1,
            frequency_hz=1_000.0,
            magnitude=-3.0,
            magnitude_unit="db",
        )
    with pytest.raises(ValueError, match="magnitude unit and semantics"):
        MarkerReading(
            index=1,
            frequency_hz=1_000.0,
            magnitude=-3.0,
            magnitude_unit="dbm",
            magnitude_semantics="relative",
        )
    with pytest.raises(ValueError, match="delta magnitude unit"):
        MarkerReading(
            index=1,
            frequency_hz=1_000.0,
            delta_magnitude=-0.5,
            delta_magnitude_unit="dbm",  # type: ignore[arg-type]
        )


def _no_op(*args, **kwargs):
    return None


class _SweepAnalyzer:
    idn = get_snapshot = fetch_frequency_response = apply_sweep_plan = _no_op
    trigger_single = set_source_output = read_markers = read_measurements = _no_op
    close = _no_op


def test_sweep_analyzer_kind_protocol_and_capabilities_are_public():
    capabilities = (
        "sweep_analyzer.idn",
        "sweep_analyzer.status",
        "sweep_analyzer.trace",
        "sweep_analyzer.configure",
        "sweep_analyzer.trigger",
        "sweep_analyzer.output",
        "sweep_analyzer.marker",
        "sweep_analyzer.analysis",
    )
    driver = _SweepAnalyzer()
    descriptor = InstrumentDescriptor(
        driver_id="example.sweep-analyzer",
        kind="sweep_analyzer",
        display_name="Example Sweep Analyzer",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities,
        idn_patterns=("EXAMPLE,EX1",),
        backends=("serial",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda context: driver,
    )

    assert "sweep_analyzer" in VALID_PLUGIN_KINDS
    assert isinstance(driver, SweepAnalyzerDriver)
    assert set(capabilities) <= set(CAPABILITY_METHODS)
    validate_declared_capabilities(descriptor, driver)

    class MissingTrace:
        idn = get_snapshot = apply_sweep_plan = _no_op
        trigger_single = set_source_output = read_markers = read_measurements = _no_op
        close = _no_op

    with pytest.raises(TypeError, match="fetch_frequency_response"):
        validate_declared_capabilities(descriptor, MissingTrace())

    metadata = descriptor.to_metadata()
    records = plugin_doctor_records(PluginRegistry((metadata,)))
    assert isinstance(metadata, InstrumentPlugin)
    assert metadata.kind == "sweep_analyzer"
    assert all(record.severity == "ok" for record in records)
