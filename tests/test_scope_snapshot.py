import io
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wavebench.cli import main
from wavebench.errors import ConfigError
from wavebench.instruments.models import (
    ScopeAnalogChannelSnapshot,
    ScopeEdgeTriggerSnapshot,
    ScopeHealthSnapshot,
    ScopeIdentitySnapshot,
    ScopeProbeSnapshot,
    ScopeSnapshot,
    ScopeTimebaseSnapshot,
    ScopeWaveformMetadataSnapshot,
)
from wavebench.services.scope_service import ScopeService


def _snapshot(channel: int = 2) -> ScopeSnapshot:
    return ScopeSnapshot(
        identity=ScopeIdentitySnapshot("Example", "EX1", "123", "1.0", ("A", "B")),
        health=ScopeHealthSnapshot(4, 8, 0, 53, 53, 5_000_000.0, True, True),
        channel=ScopeAnalogChannelSnapshot(
            channel, True, "DCL", 8.0, 1.0, 0.0, 0.0, None, "NORM", 0.0,
            "input", True, False, "SAMPLE",
        ),
        timebase=ScopeTimebaseSnapshot(0.001, 10, 0.0, 0.001, 50.0, 0.0001, False),
        probe=ScopeProbeSnapshot(channel, 10.0, None, None, 10_000_000.0, "P10", "PASSIVE"),
        waveform=ScopeWaveformMetadataSnapshot(
            channel, -0.0005, 0.0005, 1000, 1, 1e-6, -0.0005, 0.001, 0.0, 8,
        ),
        trigger=ScopeEdgeTriggerSnapshot("EDGE", channel, "AUTO", "POS", "DC", 0.1, "AUTO", "OFF", 1e-6),
    )


def test_scope_service_status_requires_capability_and_uses_one_session():
    driver = SimpleNamespace(get_snapshot=lambda channel: _snapshot(channel))
    descriptor = SimpleNamespace(driver_id="example.scope", capabilities=("scope.snapshot",))
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="example.scope")),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=descriptor,
    )

    assert service.status(channel=2) == _snapshot(2)


def test_scope_service_status_rejects_missing_capability_before_opening():
    descriptor = SimpleNamespace(driver_id="minimal.scope", capabilities=("scope.idn",))
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="minimal.scope")),
        logger=SimpleNamespace(),
        descriptor=descriptor,
    )

    with patch.object(service, "_open_scope") as open_scope:
        with pytest.raises(ConfigError, match="scope.snapshot"):
            service.status(channel=1)

    open_scope.assert_not_called()


def test_scope_service_channel_display_returns_mutation_manifest():
    calls = []

    def set_channel_display(channel, enabled, *, check_errors=True):
        calls.append((channel, enabled, check_errors))

    driver = SimpleNamespace(set_channel_display=set_channel_display)
    descriptor = SimpleNamespace(
        driver_id="example.scope",
        capabilities=("scope.channel_display",),
    )
    service = ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.scope", check_errors=False)
        ),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=descriptor,
    )

    manifest = service.set_channel_display(channel=3, enabled=False)

    assert calls == [(3, False, False)]
    assert manifest == {
        "operation": "scope.channel_display",
        "mutates_instrument": True,
        "raw_scpi": False,
        "channel": 3,
        "display": "off",
        "affected_settings": ["CH3.display"],
    }


def test_scope_service_focus_returns_auditable_mutation_manifest():
    calls = []

    def focus_channel(
        channel,
        *,
        time_range_s=None,
        vertical_scale_v_per_div=None,
        hide_other_channels=False,
        check_errors=True,
    ):
        calls.append(
            {
                "channel": channel,
                "time_range_s": time_range_s,
                "vertical_scale_v_per_div": vertical_scale_v_per_div,
                "hide_other_channels": hide_other_channels,
                "check_errors": check_errors,
            }
        )

    driver = SimpleNamespace(focus_channel=focus_channel)
    descriptor = SimpleNamespace(
        driver_id="example.scope",
        capabilities=("scope.focus_channel", "scope.errors"),
    )
    service = ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.scope", check_errors=True)
        ),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=descriptor,
    )

    manifest = service.focus_channel(
        channel=2,
        time_range_s=0.01,
        vertical_scale_v_per_div=0.2,
        hide_other_channels=True,
    )

    assert calls == [
        {
            "channel": 2,
            "time_range_s": 0.01,
            "vertical_scale_v_per_div": 0.2,
            "hide_other_channels": True,
            "check_errors": True,
        }
    ]
    assert manifest["operation"] == "scope.focus_channel"
    assert manifest["mutates_instrument"] is True
    assert manifest["raw_scpi"] is False
    assert manifest["channel"] == 2
    assert manifest["time_range_s"] == 0.01
    assert manifest["vertical_scale_v_per_div"] == 0.2
    assert manifest["hide_other_channels"] is True
    assert manifest["affected_settings"] == [
        "CH2.display",
        "timebase.range",
        "CH2.vertical_scale",
        "CH2.offset",
        "CH1.display",
        "CH3.display",
        "CH4.display",
    ]


def test_scope_status_cli_uses_default_channel_and_prints_stable_fields():
    service = SimpleNamespace(
        config=SimpleNamespace(scope=SimpleNamespace(default_channel=2)),
        status=lambda channel: _snapshot(channel),
    )
    stdout = io.StringIO()

    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(["scope", "status"])

    assert code == 0
    output = stdout.getvalue()
    assert "identity.model=EX1\n" in output
    assert "identity.options=A,B\n" in output
    assert "health.error_queue_nonempty=true\n" in output
    assert "channel.channel=2\n" in output
    assert "channel.bandwidth_hz=n/a\n" in output
    assert "trigger.source_channel=2\n" in output
