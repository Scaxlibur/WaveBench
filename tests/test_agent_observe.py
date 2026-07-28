from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pytest

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
    WaveformData,
    WaveformHeader,
)
from wavebench.services.agent_observe import scope_observe_payload


def _write_config(root: Path) -> Path:
    path = root / "wavebench.toml"
    path.write_text(
        """
[connection]
resource = "TCPIP::scope::INSTR"

[scope]
driver = "ds1104"
default_channel = 1

[waveform]
points = "def"
""",
        encoding="utf-8",
    )
    return path


def _snapshot(channel: int) -> ScopeSnapshot:
    return ScopeSnapshot(
        identity=ScopeIdentitySnapshot("RIGOL", "DS1104Z", "123", "1.0", ()),
        health=ScopeHealthSnapshot(0, 0, 0, 1, 1, 1_000_000.0, False, False),
        channel=ScopeAnalogChannelSnapshot(
            channel,
            True,
            "DC",
            8.0,
            1.0,
            0.0,
            0.0,
            None,
            "NORM",
            0.0,
            "",
            False,
            False,
            "SAMPLE",
        ),
        timebase=ScopeTimebaseSnapshot(0.001, 12, 0.0, 0.0012, 50.0, 0.0001, False),
        probe=ScopeProbeSnapshot(channel, 10.0, None, None, 1_000_000.0, "P10", "PASSIVE"),
        waveform=ScopeWaveformMetadataSnapshot(
            channel,
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
        trigger=ScopeEdgeTriggerSnapshot("EDGE", channel, "AUTO", "POS", "DC", 0.0, "AUTO", "OFF", 1e-6),
    )


class _FakeScopeService:
    def __init__(self, *, config, logger):
        self.config = config

    def idn(self):
        return "RIGOL TECHNOLOGIES,DS1104Z Plus,123,1.0"

    def status(self, channel):
        return _snapshot(channel)

    def require_high_impedance(self, channel, *, allow_50ohm=False):
        return "DC"

    def fetch_waveform(self, channel):
        return WaveformData(
            channel=channel,
            header=WaveformHeader(x_start=0.0, x_stop=0.002, points=5),
            voltages_v=np.array([0.0, 1.0, 0.0, -1.0, 0.0]),
        )


def test_scope_observe_payload_returns_structured_read_only_context():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            payload = scope_observe_payload(config_path=config, channel=2, fetch_waveform=True)

    assert payload["status"] == "ok"
    assert payload["read_only"] is True
    assert payload["mutates_instrument"] is True
    assert payload["raw_scpi"] is False
    assert payload["observation"]["channel"] == 2
    assert payload["observation"]["channels"] == [2]
    assert payload["identity"]["data"]["idn"].startswith("RIGOL")
    assert payload["scope_status"]["data"]["channel"]["channel"] == 2
    assert payload["coupling"]["data"]["accepted_for_capture"] is True
    assert payload["waveform"]["data"]["summary"]["samples"] == 5
    assert payload["waveform"]["data"]["raw_samples_included"] is False
    assert payload["channels"][0]["channel"] == 2
    assert payload["instrument_state_effects"]


def test_scope_observe_can_skip_waveform_fetch():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            payload = scope_observe_payload(config_path=config, fetch_waveform=False)

    assert payload["waveform"]["status"] == "skipped"
    assert payload["mutates_instrument"] is False
    assert payload["instrument_state_effects"] == []


def test_scope_observe_payload_supports_multiple_channels():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            payload = scope_observe_payload(config_path=config, channels=(1, 2), fetch_waveform=True)

    assert payload["observation"]["channel"] == 1
    assert payload["observation"]["channels"] == [1, 2]
    assert [item["channel"] for item in payload["channels"]] == [1, 2]
    assert payload["channels"][1]["waveform"]["data"]["summary"]["channel"] == 2
    assert payload["relationships"][0]["channels"] == [1, 2]
    assert payload["relationships"][0]["common_time"]["overlap"] is True


def test_scope_observe_payload_evaluates_channel_expectations():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            payload = scope_observe_payload(
                config_path=config,
                channel=1,
                fetch_waveform=True,
                expectations={1: {"vpp_v": 2.0, "vpp_tolerance_ratio": 0.01}},
            )

    assert payload["expectations"]["status"] == "pass"
    assert payload["channels"][0]["expectation"]["data"]["checks"][0]["metric"] == "vpp_v"


def test_scope_observe_expectations_require_explicit_waveform_fetch():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with pytest.raises(ConfigError, match="fetch_waveform=true"):
            scope_observe_payload(
                config_path=config,
                channel=1,
                fetch_waveform=False,
                expectations={1: {"vpp_v": 2.0}},
            )


def test_scope_observe_rejects_invalid_channel():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with pytest.raises(ConfigError, match="positive integer"):
            scope_observe_payload(config_path=config, channel=0)


def test_scope_observe_rejects_ambiguous_channel_arguments():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with pytest.raises(ConfigError, match="either channel or channels"):
            scope_observe_payload(config_path=config, channel=1, channels=(2,))
