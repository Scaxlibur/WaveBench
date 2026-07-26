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
    DmmDriver,
    DmmMeasurementProfileDriver,
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
    SourceDriver,
)
from wavebench.instruments.models import (
    DmmReading,
    DmmMeasurementProfile,
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


def test_driver_contracts_are_runtime_checkable():
    assert isinstance(_Scope(), ScopeDriver)
    assert isinstance(_Source(), SourceDriver)
    assert isinstance(_Power(), PowerDriver)
    assert isinstance(_Dmm(), DmmDriver)
    assert isinstance(_DmmMeasurementProfile(), DmmMeasurementProfileDriver)
    assert isinstance(_DmmVoltageConfiguration(), DmmVoltageConfigurationDriver)
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
