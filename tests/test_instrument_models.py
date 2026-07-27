from dataclasses import asdict

import numpy as np
import pytest

from wavebench.drivers.dg4202 import SourceStatus as LegacySourceStatus
from wavebench.drivers.dm3000 import DmmReading as LegacyDmmReading
from wavebench.drivers.dp800 import PowerStatus as LegacyPowerStatus
from wavebench.drivers.rtm2032 import (
    WaveformData as LegacyWaveformData,
    WaveformHeader as LegacyWaveformHeader,
)
from wavebench.instruments.contracts import (
    DmmCalculationStatisticsDriver,
    DmmCalculationStatusDriver,
    DmmDriver,
    DmmMeasurementProfileDriver,
    DmmSystemInterfaceStatusDriver,
    DmmTriggerStatusDriver,
    DmmVoltageConfigurationDriver,
    PowerDriver,
    ScopeAcquisitionStatusDriver,
    ScopeAverageCaptureDriver,
    ScopeAnalysisReadDriver,
    ScopeDriver,
    ScopeDigitalStatusDriver,
    ScopeDigitalWaveformDriver,
    ScopeHistoryTimestampsDriver,
    ScopeMeasurementStatisticsDriver,
    ScopeSnapshotDriver,
    SourceChannelProfileDriver,
    SourceCounterProfileDriver,
    SourceDriver,
    SourceSweepControlDriver,
    SourceSweepProfileDriver,
)
from wavebench.instruments.models import (
    DmmCalculationStatistics,
    DmmCalculationStatus,
    DmmReading,
    DmmMeasurementProfile,
    DmmSystemInterfaceStatus,
    DmmTriggerStatus,
    DmmDcvImpedanceConfiguration,
    DmmVoltageRangeConfiguration,
    PowerStatus,
    ScopeAnalogChannelSnapshot,
    ScopeEdgeTriggerSnapshot,
    ScopeHealthSnapshot,
    ScopeDigitalWaveform,
    ScopeDigitalWaveformRequest,
    ScopeIdentitySnapshot,
    ScopeProbeSnapshot,
    ScopeSnapshot,
    ScopeTimebaseSnapshot,
    ScopeWaveformMetadataSnapshot,
    SourceChannelProfile,
    SourceCounterMeasurement,
    SourceCounterProfile,
    SourceSweepConfiguration,
    SourceSweepProfile,
    SourceStatus,
    WaveformData,
    WaveformHeader,
)


def test_legacy_driver_model_imports_are_compatible_reexports():
    assert LegacyWaveformHeader is WaveformHeader
    assert LegacyWaveformData is WaveformData
    assert LegacySourceStatus is SourceStatus
    assert LegacyPowerStatus is PowerStatus
    assert LegacyDmmReading is DmmReading


def test_shared_models_keep_serialization_and_waveform_behavior():
    header = WaveformHeader(x_start=0.0, x_stop=2.0, points=3)
    waveform = WaveformData(channel=1, header=header, voltages_v=np.array([0.0, 1.0, 0.0]))
    reading = DmmReading(function="dcv", value=1.25, unit="V", raw="1.25")

    assert header.x_increment == 1.0
    assert waveform.times_s.tolist() == [0.0, 1.0, 2.0]
    assert waveform.sample_count == 3
    assert asdict(reading) == {"function": "dcv", "value": 1.25, "unit": "V", "raw": "1.25"}
    assert reading.as_dict() == asdict(reading)
    profile = DmmMeasurementProfile("dcv", 0, None, "10M")
    assert profile.as_dict() == asdict(profile)
    range_result = DmmVoltageRangeConfiguration("dcv", 2, 1)
    assert range_result.as_dict()["changed"] is True
    impedance_result = DmmDcvImpedanceConfiguration("10M", "10G", 2)
    assert impedance_result.as_dict()["changed"] is True
    trigger = DmmTriggerStatus("AUTO", 0.4, False, 1, 1, "RISE", "POS", 0.007)
    assert trigger.as_dict()["auto_interval_s"] == 0.4
    calculation = DmmCalculationStatus("limit", 3, 0.0, 600.0)
    assert calculation.as_dict()["function"] == "limit"
    calculation = DmmCalculationStatus("none", 0, 0.0, 600.0)
    assert calculation.as_dict()["dbm_reference_ohm"] == 600.0
    statistics = DmmCalculationStatistics("average", 1.25, 3)
    assert statistics.as_dict()["count"] == 3
    system_status = DmmSystemInterfaceStatus(
        True,
        "ENGLISH",
        "DOT",
        "NONE",
        128,
        False,
        True,
        True,
        22,
        9600,
        "NONE8BITS",
    )
    assert system_status.as_dict()["gpib_address"] == 22


def test_source_channel_profile_serializes_strict_read_only_context():
    profile = SourceChannelProfile(
        status=_source_status(),
        load_ohm=None,
        polarity="NORMAL",
        noise_enabled=False,
        noise_scale_percent=10.0,
        sync_enabled=True,
        sync_polarity="POSITIVE",
        burst_enabled=False,
        modulation_enabled=False,
        modulation_type="AM",
        marker_enabled=False,
        pulse_hold="DUTY",
    )

    assert profile.as_dict() == {
        "status": _source_status().as_dict(),
        "load_ohm": None,
        "polarity": "NORMAL",
        "noise_enabled": False,
        "noise_scale_percent": 10.0,
        "sync_enabled": True,
        "sync_polarity": "POSITIVE",
        "burst_enabled": False,
        "modulation_enabled": False,
        "modulation_type": "AM",
        "marker_enabled": False,
        "pulse_hold": "DUTY",
    }


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"load_ohm": True}, "source load"),
        ({"load_ohm": float("inf")}, "source load"),
        ({"polarity": "SIDEWAYS"}, "output polarity"),
        ({"noise_enabled": 1}, "noise_enabled"),
        ({"noise_scale_percent": float("nan")}, "noise scale"),
        ({"sync_polarity": "BOTH"}, "sync polarity"),
        ({"modulation_type": "UNKNOWN"}, "modulation type"),
        ({"pulse_hold": "BOTH"}, "pulse hold"),
    ],
)
def test_source_channel_profile_rejects_ambiguous_context(changes, message):
    values = {
        "status": _source_status(),
        "load_ohm": 50.0,
        "polarity": "NORMAL",
        "noise_enabled": False,
        "noise_scale_percent": 10.0,
        "sync_enabled": True,
        "sync_polarity": "POSITIVE",
        "burst_enabled": False,
        "modulation_enabled": False,
        "modulation_type": "AM",
        "marker_enabled": False,
        "pulse_hold": "DUTY",
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        SourceChannelProfile(**values)


def _source_sweep_profile(**changes):
    values = {
        "channel": 1,
        "enabled": False,
        "start_hz": 100.0,
        "stop_hz": 1000.0,
        "center_hz": 550.0,
        "span_hz": 900.0,
        "spacing": "LINEAR",
        "steps": 101,
        "sweep_time_s": 1.0,
        "start_hold_s": 0.0,
        "stop_hold_s": 0.0,
        "return_time_s": 0.0,
        "trigger_source": "INTERNAL",
        "trigger_slope": "POSITIVE",
        "trigger_out": "OFF",
        "marker_enabled": False,
        "marker_frequency_hz": 550.0,
    }
    values.update(changes)
    return SourceSweepProfile(**values)


def test_source_sweep_profile_serializes_complete_query_only_snapshot():
    profile = _source_sweep_profile()

    assert profile.as_dict() == {
        "channel": 1,
        "enabled": False,
        "start_hz": 100.0,
        "stop_hz": 1000.0,
        "center_hz": 550.0,
        "span_hz": 900.0,
        "spacing": "LINEAR",
        "steps": 101,
        "sweep_time_s": 1.0,
        "start_hold_s": 0.0,
        "stop_hold_s": 0.0,
        "return_time_s": 0.0,
        "trigger_source": "INTERNAL",
        "trigger_slope": "POSITIVE",
        "trigger_out": "OFF",
        "marker_enabled": False,
        "marker_frequency_hz": 550.0,
    }


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"channel": True}, "channel"),
        ({"enabled": 1}, "enabled"),
        ({"start_hz": float("nan")}, "start_hz"),
        ({"start_hz": 0.0}, "positive"),
        ({"start_hz": 1001.0}, "must not exceed"),
        ({"center_hz": 600.0}, "center frequency is inconsistent"),
        ({"span_hz": 901.0}, "span is inconsistent"),
        ({"spacing": "RANDOM"}, "spacing"),
        ({"steps": 2.0}, "steps"),
        ({"steps": 1}, "steps"),
        ({"sweep_time_s": 0.0}, "sweep time"),
        ({"start_hold_s": -1.0}, "start hold"),
        ({"return_time_s": 301.0}, "return time"),
        ({"trigger_source": "BUS"}, "trigger source"),
        ({"trigger_slope": "BOTH"}, "trigger slope"),
        ({"trigger_out": "HIGH"}, "trigger output"),
        ({"marker_frequency_hz": 1001.0}, "marker frequency"),
        ({"spacing": "STEP", "marker_enabled": True}, "step spacing"),
    ],
)
def test_source_sweep_profile_rejects_inconsistent_or_ambiguous_values(changes, message):
    with pytest.raises(ValueError, match=message):
        _source_sweep_profile(**changes)


def _source_sweep_configuration(**changes):
    values = {
        "enabled": False,
        "start_hz": 100.0,
        "stop_hz": 1000.0,
        "spacing": "LINEAR",
        "steps": 101,
        "sweep_time_s": 1.0,
        "start_hold_s": 0.0,
        "stop_hold_s": 0.0,
        "return_time_s": 0.0,
        "trigger_source": "INTERNAL",
        "trigger_slope": "POSITIVE",
        "trigger_out": "OFF",
        "marker_enabled": False,
        "marker_frequency_hz": 550.0,
    }
    values.update(changes)
    return SourceSweepConfiguration(**values)


def test_source_sweep_configuration_serializes_one_start_stop_window():
    configuration = _source_sweep_configuration()

    assert configuration.frequency_basis == "START_STOP"
    assert configuration.effective_start_hz == 100.0
    assert configuration.effective_stop_hz == 1000.0
    assert configuration.as_dict() == {
        "enabled": False,
        "frequency_basis": "START_STOP",
        "start_hz": 100.0,
        "stop_hz": 1000.0,
        "center_hz": None,
        "span_hz": None,
        "spacing": "LINEAR",
        "steps": 101,
        "sweep_time_s": 1.0,
        "start_hold_s": 0.0,
        "stop_hold_s": 0.0,
        "return_time_s": 0.0,
        "trigger_source": "INTERNAL",
        "trigger_slope": "POSITIVE",
        "trigger_out": "OFF",
        "marker_enabled": False,
        "marker_frequency_hz": 550.0,
    }


def test_source_sweep_configuration_accepts_center_span_without_duplicate_window():
    configuration = _source_sweep_configuration(
        start_hz=None,
        stop_hz=None,
        center_hz=550.0,
        span_hz=900.0,
    )

    assert configuration.frequency_basis == "CENTER_SPAN"
    assert configuration.effective_start_hz == 100.0
    assert configuration.effective_stop_hz == 1000.0


def test_source_sweep_configuration_accepts_a_restorable_zero_span_window():
    configuration = _source_sweep_configuration(
        start_hz=1000.0,
        stop_hz=1000.0,
        marker_frequency_hz=1000.0,
    )

    assert configuration.effective_start_hz == 1000.0
    assert configuration.effective_stop_hz == 1000.0


def test_source_sweep_configuration_can_restore_a_complete_profile():
    profile = _source_sweep_profile(enabled=True, spacing="LOGARITHMIC")

    configuration = SourceSweepConfiguration.from_profile(profile)

    assert configuration.enabled is True
    assert configuration.frequency_basis == "START_STOP"
    assert configuration.spacing == "LOGARITHMIC"
    assert configuration.effective_start_hz == profile.start_hz
    assert configuration.effective_stop_hz == profile.stop_hz


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"start_hz": None, "stop_hz": None}, "exactly one frequency window"),
        ({"stop_hz": None}, "both start_hz and stop_hz"),
        (
            {"center_hz": 550.0, "span_hz": 900.0},
            "exactly one frequency window",
        ),
        (
            {"start_hz": None, "stop_hz": None, "center_hz": 550.0},
            "both center_hz and span_hz",
        ),
        ({"start_hz": float("nan")}, "start_hz must be finite"),
        ({"start_hz": True}, "start_hz must be finite"),
        ({"start_hz": 1001.0, "stop_hz": 1000.0}, "must not exceed"),
        (
            {
                "start_hz": None,
                "stop_hz": None,
                "center_hz": 100.0,
                "span_hz": 200.0,
            },
            "positive",
        ),
        ({"spacing": "STEP", "marker_enabled": True}, "step spacing"),
        ({"steps": 1}, "steps"),
        ({"sweep_time_s": 0.0}, "sweep time"),
        ({"trigger_source": "BUS"}, "trigger source"),
        ({"marker_frequency_hz": 1001.0}, "marker frequency"),
    ],
)
def test_source_sweep_configuration_rejects_ambiguous_or_unsafe_targets(changes, message):
    with pytest.raises(ValueError, match=message):
        _source_sweep_configuration(**changes)


def test_source_sweep_configuration_rejects_non_profile_restore_source():
    with pytest.raises(ValueError, match="SourceSweepProfile"):
        SourceSweepConfiguration.from_profile(object())


def _source_counter_measurement(**changes):
    values = {
        "frequency_hz": 1000.0,
        "period_s": 0.001,
        "duty_cycle_percent": 40.0,
        "positive_width_s": 0.0004,
        "negative_width_s": 0.0006,
    }
    values.update(changes)
    return SourceCounterMeasurement(**values)


def _source_counter_profile(**changes):
    values = {
        "enabled": False,
        "measurement": None,
        "coupling": "AC",
        "impedance_ohm": 1_000_000.0,
        "attenuation": 1,
        "gate_time": "USER1",
        "high_frequency_rejection_enabled": False,
        "trigger_level_v": 0.0,
        "sensitivity_percent": 50.0,
        "statistics_enabled": False,
        "statistics_display": "DIGITAL",
    }
    values.update(changes)
    return SourceCounterProfile(**values)


def test_source_counter_profile_serializes_off_state_without_measurement():
    profile = _source_counter_profile()

    assert profile.as_dict() == {
        "enabled": False,
        "measurement": None,
        "coupling": "AC",
        "impedance_ohm": 1_000_000.0,
        "attenuation": 1,
        "gate_time": "USER1",
        "high_frequency_rejection_enabled": False,
        "trigger_level_v": 0.0,
        "sensitivity_percent": 50.0,
        "statistics_enabled": False,
        "statistics_display": "DIGITAL",
    }


def test_source_counter_profile_serializes_complete_enabled_measurement():
    measurement = _source_counter_measurement()
    profile = _source_counter_profile(enabled=True, measurement=measurement)

    assert profile.as_dict()["measurement"] == measurement.as_dict()


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"frequency_hz": float("nan")}, "frequency_hz"),
        ({"period_s": 0.0}, "positive"),
        ({"duty_cycle_percent": 101.0}, "duty cycle"),
        ({"positive_width_s": -1.0}, "pulse widths"),
        ({"period_s": 0.002}, "frequency and period"),
        ({"positive_width_s": 0.0005}, "pulse widths"),
        ({"duty_cycle_percent": 50.0}, "duty cycle"),
    ],
)
def test_source_counter_measurement_rejects_inconsistent_values(changes, message):
    with pytest.raises(ValueError, match=message):
        _source_counter_measurement(**changes)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"enabled": 1}, "enabled"),
        ({"enabled": True}, "measurement must be present"),
        ({"measurement": _source_counter_measurement()}, "measurement must be present"),
        ({"coupling": "GND"}, "coupling"),
        ({"impedance_ohm": 51.0}, "impedance"),
        ({"attenuation": 2}, "attenuation"),
        ({"gate_time": "USER7"}, "gate time"),
        ({"high_frequency_rejection_enabled": 1}, "high_frequency"),
        ({"trigger_level_v": float("inf")}, "trigger level"),
        ({"trigger_level_v": 2.51}, "trigger level"),
        ({"sensitivity_percent": 100.1}, "sensitivity"),
        ({"statistics_enabled": "OFF"}, "statistics_enabled"),
        ({"statistics_display": "GRAPH"}, "statistics display"),
    ],
)
def test_source_counter_profile_rejects_ambiguous_values(changes, message):
    with pytest.raises(ValueError, match=message):
        _source_counter_profile(**changes)


def test_driver_contracts_are_runtime_checkable():
    assert isinstance(_Scope(), ScopeDriver)
    assert isinstance(_Source(), SourceDriver)
    assert isinstance(_Power(), PowerDriver)
    assert isinstance(_Dmm(), DmmDriver)
    assert isinstance(_DmmMeasurementProfile(), DmmMeasurementProfileDriver)
    assert isinstance(_DmmTriggerStatus(), DmmTriggerStatusDriver)
    assert isinstance(_DmmCalculationStatus(), DmmCalculationStatusDriver)
    assert isinstance(_DmmCalculationStatistics(), DmmCalculationStatisticsDriver)
    assert isinstance(_DmmSystemInterfaceStatus(), DmmSystemInterfaceStatusDriver)
    assert isinstance(_DmmVoltageConfiguration(), DmmVoltageConfigurationDriver)
    assert isinstance(_SourceChannelProfile(), SourceChannelProfileDriver)
    assert isinstance(_SourceCounterProfile(), SourceCounterProfileDriver)
    assert isinstance(_SourceSweepProfile(), SourceSweepProfileDriver)
    assert isinstance(_SourceSweepControl(), SourceSweepControlDriver)
    assert isinstance(_ScopeSnapshot(), ScopeSnapshotDriver)
    assert isinstance(_ScopeAcquisitionStatus(), ScopeAcquisitionStatusDriver)
    assert isinstance(_ScopeAverageCapture(), ScopeAverageCaptureDriver)
    assert isinstance(_ScopeDigitalStatus(), ScopeDigitalStatusDriver)
    assert isinstance(_ScopeDigitalWaveform(), ScopeDigitalWaveformDriver)
    assert isinstance(_ScopeAnalysisRead(), ScopeAnalysisReadDriver)
    assert isinstance(_ScopeHistoryTimestamps(), ScopeHistoryTimestampsDriver)
    assert isinstance(_ScopeMeasurementStatistics(), ScopeMeasurementStatisticsDriver)


def test_scope_snapshot_keeps_typed_read_only_sections():
    snapshot = _scope_snapshot()

    assert snapshot.identity.model == "EX1"
    assert snapshot.channel.channel == 2
    assert snapshot.waveform.points == 1000
    assert asdict(snapshot)["trigger"]["source_channel"] == 2


class _DynamicDriver:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _Scope(_DynamicDriver):
    idn = close = errors = channel_coupling = autoscale = fetch_waveform = capture_waveform = (
        screenshot_png
    ) = lambda *args, **kwargs: None


class _Source(_DynamicDriver):
    idn = close = errors = assert_no_errors = get_status = set_frequency = set_output = (
        set_function
    ) = set_amplitude_vpp = set_square_duty_cycle = upload_dg4000_dac14_block = (
        probe_arbitrary_queries
    ) = lambda *args, **kwargs: None


class _SourceChannelProfile(_DynamicDriver):
    idn = close = get_channel_profile = lambda *args, **kwargs: None


class _SourceSweepProfile(_DynamicDriver):
    idn = close = get_sweep_profile = lambda *args, **kwargs: None


class _SourceSweepControl(_DynamicDriver):
    idn = close = configure_sweep = trigger_sweep = lambda *args, **kwargs: None


class _SourceCounterProfile(_DynamicDriver):
    idn = close = get_counter_profile = lambda *args, **kwargs: None


def _source_status() -> SourceStatus:
    return SourceStatus(
        channel=1,
        output="OFF",
        function="SIN",
        frequency_hz=1_000.0,
        amplitude=2.0,
        amplitude_unit="VPP",
        offset_v=0.0,
        phase_deg=0.0,
        frequency_mode="FIX",
        sweep_enabled="OFF",
        apply_raw='"SIN,1000,2,0,0"',
    )


class _Power(_DynamicDriver):
    idn = close = get_status = get_measurement = get_protection_status = set_protection = (
        set_voltage_current_limit
    ) = set_output = lambda *args, **kwargs: None


class _Dmm(_DynamicDriver):
    idn = close = function_status = set_function = apply_function = read = lambda *args, **kwargs: (
        None
    )


class _DmmMeasurementProfile(_DynamicDriver):
    idn = close = measurement_profile = lambda *args, **kwargs: None


class _DmmTriggerStatus(_DynamicDriver):
    idn = close = trigger_status = lambda *args, **kwargs: None


class _DmmCalculationStatus(_DynamicDriver):
    idn = close = calculation_status = lambda *args, **kwargs: None


class _DmmCalculationStatistics(_DynamicDriver):
    idn = close = calculation_statistics = lambda *args, **kwargs: None


class _DmmSystemInterfaceStatus(_DynamicDriver):
    idn = close = system_interface_status = lambda *args, **kwargs: None


class _DmmVoltageConfiguration(_DynamicDriver):
    idn = close = set_voltage_range = set_dcv_impedance = lambda *args, **kwargs: None


class _ScopeSnapshot(_DynamicDriver):
    idn = close = get_snapshot = lambda *args, **kwargs: None


class _ScopeAcquisitionStatus(_DynamicDriver):
    idn = close = get_acquisition_status = lambda *args, **kwargs: None


class _ScopeAverageCapture(_DynamicDriver):
    idn = close = capture_average = lambda *args, **kwargs: None


class _ScopeDigitalStatus(_DynamicDriver):
    idn = close = get_digital_status = lambda *args, **kwargs: None


class _ScopeDigitalWaveform(_DynamicDriver):
    idn = close = get_digital_waveform = lambda *args, **kwargs: None


def test_digital_waveform_request_and_model_validate_packed_uint16_semantics():
    request = ScopeDigitalWaveformRequest((0, 3, 15), True)
    waveform = ScopeDigitalWaveform(
        channels=request.channels,
        x_start_s=-1e-6,
        x_stop_s=1e-6,
        x_increment_s=1e-6,
        samples=np.array([0, 1, 9 | (1 << 15)], dtype=np.uint16),
    )

    assert waveform.sample_count == 3
    assert waveform.samples.dtype == np.uint16
    assert waveform.samples.flags.writeable is False
    assert waveform.times_s.tolist() == [-1e-6, 0.0, 1e-6]


@pytest.mark.parametrize("channels", [(), (0, 0), (-1,), (16,), (True,)])
def test_digital_waveform_request_rejects_invalid_channels(channels):
    with pytest.raises(ValueError):
        ScopeDigitalWaveformRequest(channels, True)


def test_digital_waveform_rejects_bits_outside_requested_channels():
    with pytest.raises(ValueError, match="outside"):
        ScopeDigitalWaveform(
            channels=(0,),
            x_start_s=0.0,
            x_stop_s=0.0,
            x_increment_s=1.0,
            samples=np.array([2], dtype=np.uint16),
        )


def test_digital_waveform_rejects_axis_length_mismatch():
    with pytest.raises(ValueError, match="sample count"):
        ScopeDigitalWaveform(
            channels=(0,),
            x_start_s=0.0,
            x_stop_s=5.0,
            x_increment_s=1.0,
            samples=np.array([0, 1], dtype=np.uint16),
        )


@pytest.mark.parametrize(
    "samples, message",
    [
        (np.array([], dtype=np.uint16), "nonempty"),
        (np.array([1.5]), "integers"),
        (np.array([-1]), "uint16"),
        (np.array([65536]), "uint16"),
    ],
)
def test_digital_waveform_rejects_invalid_packed_samples(samples, message):
    with pytest.raises(ValueError, match=message):
        ScopeDigitalWaveform(
            channels=tuple(range(16)),
            x_start_s=0.0,
            x_stop_s=0.0,
            x_increment_s=1.0,
            samples=samples,
        )


class _ScopeAnalysisRead(_DynamicDriver):
    idn = close = lambda *args, **kwargs: None
    get_math_waveform_metadata = get_fft_status = lambda *args, **kwargs: None
    get_reference_waveform_metadata = get_cursor_readout = lambda *args, **kwargs: None


class _ScopeHistoryTimestamps(_DynamicDriver):
    idn = close = get_history_timestamps = lambda *args, **kwargs: None


class _ScopeMeasurementStatistics(_DynamicDriver):
    idn = close = get_measurement_statistics = lambda *args, **kwargs: None


def _scope_snapshot() -> ScopeSnapshot:
    return ScopeSnapshot(
        identity=ScopeIdentitySnapshot("Example", "EX1", "123", "1.0", ("OPT",)),
        health=ScopeHealthSnapshot(0, 0, 0, 1, 1, 1_000_000.0, False, False),
        channel=ScopeAnalogChannelSnapshot(
            2,
            True,
            "DCL",
            8.0,
            1.0,
            0.0,
            0.0,
            None,
            "NORM",
            0.0,
            "input",
            True,
            False,
            "SAMPLE",
        ),
        timebase=ScopeTimebaseSnapshot(0.001, 10, 0.0, 0.001, 50.0, 0.0001, False),
        probe=ScopeProbeSnapshot(2, 10.0, None, None, 10_000_000.0, "P10", "PASSIVE"),
        waveform=ScopeWaveformMetadataSnapshot(
            2,
            -0.0005,
            0.0005,
            1000,
            1,
            1e-6,
            -0.0005,
            0.001,
            0.0,
            8,
        ),
        trigger=ScopeEdgeTriggerSnapshot("EDGE", 2, "AUTO", "POS", "DC", 0.1, "AUTO", "OFF", 1e-6),
    )
