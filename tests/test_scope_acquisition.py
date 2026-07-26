import io
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wavebench.cli import main
from wavebench.errors import ConfigError
from wavebench.instruments.models import (
    ScopeAcquisitionStatus,
    ScopeCursorReadout,
    ScopeDerivedWaveformMetadata,
    ScopeFftStatus,
    ScopeHistoryTimestamp,
    ScopeHistoryTimestamps,
    ScopeMeasurementStatistics,
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
