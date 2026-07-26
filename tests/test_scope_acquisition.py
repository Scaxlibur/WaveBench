import io
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from wavebench.cli import main
from wavebench.errors import ConfigError
from wavebench.instruments.models import (
    ScopeAcquisitionStatus,
    ScopeAverageCaptureRequest,
    ScopeAverageCaptureResult,
    ScopeAverageConfiguration,
    ScopeCursorReadout,
    ScopeDerivedWaveformMetadata,
    ScopeFftStatus,
    ScopeHistoryTimestamp,
    ScopeHistoryTimestamps,
    ScopeMeasurementStatistics,
    WaveformData,
    WaveformHeader,
)
from wavebench.services.scope_service import ScopeService


def _service(*, capability: str, method_name: str, result):
    driver = SimpleNamespace(**{method_name: lambda *args, **kwargs: result})
    descriptor = SimpleNamespace(driver_id="example.scope", capabilities=(capability,))
    return ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="example.scope")),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=descriptor,
    )


def test_scope_service_acquisition_status_uses_optional_capability():
    expected = ScopeAcquisitionStatus(16, True, True, False, True, 1000, 24)
    service = _service(
        capability="scope.acquisition_status",
        method_name="get_acquisition_status",
        result=expected,
    )

    assert service.acquisition_status() == expected


def test_scope_service_average_capture_builds_request_and_uses_optional_capability():
    request = ScopeAverageCaptureRequest((1, 2), 16, True)
    configuration = ScopeAverageConfiguration(8, 1, ((1, "OFF"), (2, "OFF")))
    waveforms = (SimpleNamespace(channel=1), SimpleNamespace(channel=2))
    expected = ScopeAverageCaptureResult(
        request=request,
        waveforms=waveforms,
        average_complete=True,
        configuration_before=configuration,
        configuration_after=configuration,
        restored_fields=(
            "ACQuire:AVERage:COUNt",
            "ACQuire:NSINgle:COUNt",
            "CHANnel:ARITHmetics",
        ),
    )
    calls = []
    driver = SimpleNamespace(
        channel_coupling=lambda channel: calls.append(("coupling", channel)) or "DCL",
        capture_average=lambda value: calls.append(("capture", value)) or expected,
    )
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="example.scope")),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.scope",
            capabilities=("scope.capture_average", "scope.channel_coupling"),
            scope_coupling_policy="switchable-termination",
        ),
    )

    assert service.capture_average(
        channels=(1, 2),
        average_count=16,
        acquisition_stopped=True,
    ) == expected
    assert calls == [("coupling", 1), ("coupling", 2), ("capture", request)]


def test_scope_average_capture_fails_before_opening_without_capability():
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="minimal.scope")),
        logger=SimpleNamespace(),
        descriptor=SimpleNamespace(driver_id="minimal.scope", capabilities=("scope.idn",)),
    )

    with patch.object(service, "_open_scope") as open_scope:
        with pytest.raises(ConfigError, match="scope.capture_average"):
            service.capture_average(
                channels=(1,),
                average_count=8,
                acquisition_stopped=True,
            )

    open_scope.assert_not_called()


def test_scope_average_capture_checks_coupling_in_same_session_before_writes():
    calls = []
    driver = SimpleNamespace(
        channel_coupling=lambda channel: calls.append(("coupling", channel)) or "DC",
        capture_average=lambda request: calls.append(("capture", request)),
    )
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="example.scope")),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.scope",
            capabilities=("scope.capture_average", "scope.channel_coupling"),
            scope_coupling_policy="switchable-termination",
        ),
    )

    with pytest.raises(ConfigError, match="50 ohm"):
        service.capture_average(
            channels=(1,),
            average_count=8,
            acquisition_stopped=True,
        )

    assert calls == [("coupling", 1)]


@pytest.mark.parametrize(
    ("channels", "average_count", "message"),
    [
        ((), 8, "at least one"),
        ((1, 1), 8, "unique"),
        ((0,), 8, "positive"),
        ((1.5,), 8, "positive"),
        ((1,), 3, "power of two"),
        ((1,), 8.0, "power of two"),
        ((1,), 2048, "power of two"),
    ],
)
def test_scope_average_capture_request_rejects_invalid_values(
    channels,
    average_count,
    message,
):
    with pytest.raises(ValueError, match=message):
        ScopeAverageCaptureRequest(channels, average_count, True)


def test_scope_average_capture_request_requires_stopped_confirmation():
    with pytest.raises(ValueError, match="acquisition is stopped"):
        ScopeAverageCaptureRequest((1,), 8, False)


def test_scope_average_capture_cli_checks_inputs_and_prints_restore_evidence():
    request = ScopeAverageCaptureRequest((1, 2), 16, True)
    configuration = ScopeAverageConfiguration(8, 1, ((1, "OFF"), (2, "OFF")))
    waveforms = tuple(
        WaveformData(
            channel=channel,
            header=WaveformHeader(0.0, 1.0, 2),
            voltages_v=np.array([0.0, 1.0]),
        )
        for channel in (1, 2)
    )
    result = ScopeAverageCaptureResult(
        request=request,
        waveforms=waveforms,
        average_complete=True,
        configuration_before=configuration,
        configuration_after=configuration,
        restored_fields=(
            "ACQuire:AVERage:COUNt",
            "ACQuire:NSINgle:COUNt",
            "CHANnel:ARITHmetics",
        ),
    )
    calls = []
    service = SimpleNamespace(
        capture_average=lambda **kwargs: calls.append(kwargs) or result,
    )
    stdout = io.StringIO()

    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(
            [
                "scope",
                "capture-average",
                "--channel",
                "1",
                "--channel",
                "2",
                "--average-count",
                "16",
                "--acquisition-stopped",
            ]
        )

    assert code == 0
    assert calls == [
        {
            "channels": (1, 2),
            "average_count": 16,
            "acquisition_stopped": True,
            "allow_50ohm": False,
        }
    ]
    assert stdout.getvalue().splitlines() == [
        "average.channels=1,2",
        "average.count=16",
        "average.complete=true",
        "average.restored=true",
        "average.restored_fields=ACQuire:AVERage:COUNt,ACQuire:NSINgle:COUNt,CHANnel:ARITHmetics",
        "average.channel.1.samples=2",
        "average.channel.2.samples=2",
    ]


def test_scope_service_history_timestamps_uses_optional_capability():
    expected = ScopeHistoryTimestamps(
        channel=2,
        entries=(ScopeHistoryTimestamp(1, 0.0, 2026, 7, 26, 10, 30, 1.25),),
    )
    service = _service(
        capability="scope.history_timestamps",
        method_name="get_history_timestamps",
        result=expected,
    )

    assert service.history_timestamps(channel=2) == expected


@pytest.mark.parametrize(
    ("capability", "call"),
    [
        ("scope.acquisition_status", lambda service: service.acquisition_status()),
        ("scope.history_timestamps", lambda service: service.history_timestamps(1)),
    ],
)
def test_scope_optional_queries_fail_before_opening(capability, call):
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="minimal.scope")),
        logger=SimpleNamespace(),
        descriptor=SimpleNamespace(driver_id="minimal.scope", capabilities=("scope.idn",)),
    )

    with patch.object(service, "_open_scope") as open_scope:
        with pytest.raises(ConfigError, match=capability):
            call(service)

    open_scope.assert_not_called()


def test_scope_acquisition_status_cli_prints_unavailable_k15_fields():
    status = ScopeAcquisitionStatus(8, False, False, None, None, None, None)
    service = SimpleNamespace(acquisition_status=lambda: status)
    stdout = io.StringIO()

    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(["scope", "acquisition-status"])

    assert code == 0
    assert stdout.getvalue().splitlines() == [
        "average.count=8",
        "average.complete=false",
        "segmented.option_installed=false",
        "segmented.enabled=n/a",
        "segmented.maximum_enabled=n/a",
        "segmented.capacity=n/a",
        "segmented.available=n/a",
    ]


def test_scope_history_timestamps_cli_uses_default_channel_and_stable_rows():
    table = ScopeHistoryTimestamps(
        channel=2,
        entries=(
            ScopeHistoryTimestamp(1, -0.25, 2026, 7, 26, 10, 30, 1.25),
            ScopeHistoryTimestamp(2, -0.0, 2026, 7, 26, 10, 30, 1.5),
        ),
    )
    service = SimpleNamespace(
        config=SimpleNamespace(scope=SimpleNamespace(default_channel=2)),
        history_timestamps=lambda channel: table,
    )
    stdout = io.StringIO()

    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(["scope", "history-timestamps"])

    assert code == 0
    output = stdout.getvalue()
    assert "history.channel=2\n" in output
    assert "history.count=2\n" in output
    assert "history.1.relative_s=-0.25\n" in output
    assert "history.1.date=2026-07-26\n" in output
    assert "history.1.time=10:30:1.25\n" in output


def test_scope_service_measurement_statistics_forwards_explicit_guards():
    expected = ScopeMeasurementStatistics(
        2, "AMPT", 1.0, 0.9, 0.1, 0.7, 1.1, 42, (0.8, 1.0)
    )
    calls = []
    driver = SimpleNamespace(
        get_measurement_statistics=lambda *args, **kwargs: (
            calls.append((args, kwargs)) or expected
        )
    )
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="example.scope")),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.scope",
            capabilities=("scope.measurement_statistics",),
        ),
    )

    assert service.measurement_statistics(
        2,
        configured_slot=True,
        include_buffer=True,
        acquisition_stopped=True,
    ) == expected
    assert calls == [
        (
            (2,),
            {
                "configured_slot": True,
                "include_buffer": True,
                "acquisition_stopped": True,
            },
        )
    ]


def test_scope_measurement_statistics_fails_before_opening_without_capability():
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="minimal.scope")),
        logger=SimpleNamespace(),
        descriptor=SimpleNamespace(driver_id="minimal.scope", capabilities=("scope.idn",)),
    )

    with patch.object(service, "_open_scope") as open_scope:
        with pytest.raises(ConfigError, match="scope.measurement_statistics"):
            service.measurement_statistics(1, configured_slot=True)

    open_scope.assert_not_called()


def test_scope_measurement_statistics_cli_forwards_guards_and_prints_nan_as_na():
    stats = ScopeMeasurementStatistics(1, "AMPT", None, 2.0, 0.1, 1.5, 2.5, 20)
    calls = []
    service = SimpleNamespace(
        measurement_statistics=lambda *args, **kwargs: (
            calls.append((args, kwargs)) or stats
        )
    )
    stdout = io.StringIO()

    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(
            [
                "scope",
                "measurement-statistics",
                "--slot",
                "1",
                "--configured-slot",
            ]
        )

    assert code == 0
    assert calls == [
        (
            (1,),
            {
                "configured_slot": True,
                "include_buffer": False,
                "acquisition_stopped": False,
            },
        )
    ]
    assert stdout.getvalue().splitlines() == [
        "measurement.slot=1",
        "measurement.category=AMPT",
        "measurement.actual=n/a",
        "measurement.average=2",
        "measurement.standard_deviation=0.1",
        "measurement.minimum=1.5",
        "measurement.maximum=2.5",
        "measurement.waveform_count=20",
        "measurement.buffer=n/a",
    ]


@pytest.mark.parametrize(
    ("capability", "method_name", "service_call", "expected"),
    [
        (
            "scope.math_metadata",
            "get_math_waveform_metadata",
            lambda service: service.math_waveform_metadata(2),
            ScopeDerivedWaveformMetadata(
                "math", 2, None, 0.0, 1.0, 2, 1, 1.0, 0.0, 0.5, 0.0, 32
            ),
        ),
        (
            "scope.fft_status",
            "get_fft_status",
            lambda service: service.fft_status(2, configured_fft=True),
            ScopeFftStatus(2, True, 10.0, 1_000.0),
        ),
        (
            "scope.reference_metadata",
            "get_reference_waveform_metadata",
            lambda service: service.reference_waveform_metadata(3),
            ScopeDerivedWaveformMetadata(
                "reference", 3, "CH1", 0.0, 1.0, 2, 1, 1.0, 0.0, 0.5, 0.0, 32
            ),
        ),
        (
            "scope.cursor_readout",
            "get_cursor_readout",
            lambda service: service.cursor_readout(1, configured_cursor=True),
            ScopeCursorReadout(1, "CH1", "VERTICAL", x_delta_s=0.001),
        ),
    ],
)
def test_scope_analysis_services_use_optional_capabilities(
    capability,
    method_name,
    service_call,
    expected,
):
    service = _service(capability=capability, method_name=method_name, result=expected)
    assert service_call(service) == expected


def test_scope_math_metadata_cli_prints_stable_fields():
    metadata = ScopeDerivedWaveformMetadata(
        "math", 2, None, -1.0, 1.0, 3, 1, 1.0, -1.0, 0.5, 0.0, 32
    )
    service = SimpleNamespace(math_waveform_metadata=lambda index: metadata)
    stdout = io.StringIO()

    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(["scope", "math-metadata", "--index", "2"])

    assert code == 0
    assert "math.index=2\n" in stdout.getvalue()
    assert "math.source_catalog=n/a\n" in stdout.getvalue()
    assert "math.points=3\n" in stdout.getvalue()


def test_scope_fft_status_cli_forwards_configured_guard():
    status = ScopeFftStatus(2, True, 10.0, 1_000.0)
    calls = []
    service = SimpleNamespace(
        fft_status=lambda *args, **kwargs: calls.append((args, kwargs)) or status
    )
    stdout = io.StringIO()

    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(["scope", "fft-status", "--index", "2", "--configured-fft"])

    assert code == 0
    assert calls == [((2,), {"configured_fft": True})]
    assert "fft.average_complete=true\n" in stdout.getvalue()


def test_scope_cursor_readout_cli_forwards_configured_guard():
    readout = ScopeCursorReadout(1, "CH1", "VERTICAL", x_delta_s=0.001)
    calls = []
    service = SimpleNamespace(
        cursor_readout=lambda *args, **kwargs: calls.append((args, kwargs)) or readout
    )
    stdout = io.StringIO()

    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(["scope", "cursor-readout", "--configured-cursor"])

    assert code == 0
    assert calls == [((1,), {"configured_cursor": True})]
    assert "cursor.x_delta_s=0.001\n" in stdout.getvalue()
