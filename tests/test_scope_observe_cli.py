import io
import json
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pytest

from wavebench.cli import _run_scope_observe, main
from wavebench.cli_parser import build_parser
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
            channel, True, "DC", 8.0, 1.0, 0.0, 0.0, None, "NORM", 0.0, "", False, False, "SAMPLE"
        ),
        timebase=ScopeTimebaseSnapshot(0.001, 12, 0.0, 0.0012, 50.0, 0.0001, False),
        probe=ScopeProbeSnapshot(channel, 10.0, None, None, 1_000_000.0, "P10", "PASSIVE"),
        waveform=ScopeWaveformMetadataSnapshot(
            channel, -0.0005, 0.0005, 1000, 1, 1e-6, -0.0005, 0.001, 0.0, 8
        ),
        trigger=ScopeEdgeTriggerSnapshot("EDGE", channel, "AUTO", "POS", "DC", 0.0, "AUTO", "OFF", 1e-6),
    )


class _FakeScopeService:
    instances: list["_FakeScopeService"] = []

    def __init__(self, *, config, logger):
        self.config = config
        self.session_state = None
        self.fetched_channels: list[int] = []
        _FakeScopeService.instances.append(self)

    def session_context(self, **kwargs):
        return nullcontext(self)

    def validate_observation_access(self):
        pass

    def validate_observation_fetch(self):
        pass

    def preflight_observation_fetch(self, channels, *, allow_50ohm=False):
        for channel in channels:
            self.require_high_impedance(channel, allow_50ohm=allow_50ohm)

    def observation_identity(self):
        return self.idn()

    def observation_status(self, channel):
        return asdict(self.status(channel))

    def observation_input_safety(self, channel, *, allow_50ohm=False):
        return {"channel": channel,
                "coupling": self.require_high_impedance(channel, allow_50ohm=allow_50ohm),
                "accepted_for_capture": True}

    def idn(self):
        return "RIGOL TECHNOLOGIES,DS1104Z Plus,123,1.0"

    def status(self, channel):
        return _snapshot(channel)

    def require_high_impedance(self, channel, *, allow_50ohm=False):
        return "DC"

    def fetch_waveform(self, channel):
        self.fetched_channels.append(channel)
        times = np.linspace(0.0, 0.005, 2000)
        return WaveformData(
            channel=channel,
            header=WaveformHeader(x_start=0.0, x_stop=0.005, points=2000),
            voltages_v=np.sin(2 * np.pi * 1000 * times),
        )


def _run(argv: list[str]) -> tuple[int, str, str]:
    _FakeScopeService.instances = []
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def test_scope_observe_read_only_prints_state_and_never_fetches_waveform():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        code, out, _ = _run(["scope", "observe", "--channel", "1", "--config", str(config)])

    assert code == 0
    assert "read_only=True" in out
    assert "mutates_instrument=False" in out
    assert "ch1 coupling=DC" in out
    assert "waveform" not in out
    assert _FakeScopeService.instances[0].fetched_channels == []


def test_scope_observe_fetch_waveform_evaluates_expectations_and_recommends():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _write_config(root)
        expectation = root / "expect.toml"
        expectation.write_text("[channels.1]\nfrequency_hz = 1000.0\n", encoding="utf-8")
        code, out, _ = _run(
            [
                "scope",
                "observe",
                "--channel",
                "1",
                "--fetch-waveform",
                "--expect",
                str(expectation),
                "--config",
                str(config),
            ]
        )

    assert code == 0
    assert _FakeScopeService.instances[0].fetched_channels == [1]
    assert "mutates_instrument=True" in out
    assert "ch1 expectation=ok" in out
    assert "check=frequency_hz status=pass" in out
    assert "recommendation" in out


def test_scope_observe_rejects_expect_file_without_fetch_waveform():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _write_config(root)
        expectation = root / "expect.toml"
        expectation.write_text("[channels.1]\nfrequency_hz = 1000.0\n", encoding="utf-8")
        code, _, err = _run(
            ["scope", "observe", "--expect", str(expectation), "--config", str(config)]
        )

    assert code != 0
    assert "--fetch-waveform" in err
    assert _FakeScopeService.instances == []


def test_scope_observe_rejects_invalid_expectation_before_any_instrument_io():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _write_config(root)
        expectation = root / "expect.toml"
        expectation.write_text("[channels.1]\nfrequncy_hz = 1000.0\n", encoding="utf-8")
        code, _, err = _run(
            [
                "scope",
                "observe",
                "--channel",
                "1",
                "--fetch-waveform",
                "--expect",
                str(expectation),
                "--config",
                str(config),
            ]
        )

    assert code != 0
    assert "unknown expectation field" in err
    # 校验失败必须发生在打开仪器之前
    assert _FakeScopeService.instances == []


def test_scope_observe_rejects_expectation_for_unobserved_channel():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _write_config(root)
        expectation = root / "expect.toml"
        expectation.write_text("[channels.2]\nfrequency_hz = 1000.0\n", encoding="utf-8")
        code, _, err = _run(
            [
                "scope",
                "observe",
                "--channel",
                "1",
                "--fetch-waveform",
                "--expect",
                str(expectation),
                "--config",
                str(config),
            ]
        )

    assert code != 0
    assert "expectation channels must be observed channels" in err


def test_json_scope_observe_wraps_result_in_versioned_envelope():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        code, out, _ = _run(["--json", "scope", "observe", "--channel", "1", "--config", str(config)])

    assert code == 0
    envelope = json.loads(out)
    assert envelope["schema"] == "wavebench.cli.result.v1"
    assert envelope["result"]["read_only"] is True
    assert envelope["result"]["observation"]["channels"] == [1]


@pytest.mark.parametrize("option", ["--target-cycles", "--target-vertical-divisions"])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
@pytest.mark.parametrize("fetch_waveform", [False, True])
def test_scope_observe_rejects_invalid_targets_before_loading_config(option, value, fetch_waveform):
    argv = ["scope", "observe", f"{option}={value}"]
    if fetch_waveform:
        argv.append("--fetch-waveform")
    with patch("wavebench.services.agent_observe.load_config") as load:
        code, _, err = _run(argv)

    assert code != 0
    assert option.removeprefix("--").replace("-", "_") in err
    load.assert_not_called()
    assert _FakeScopeService.instances == []


@pytest.mark.parametrize("target", ["target_cycles", "target_vertical_divisions"])
@pytest.mark.parametrize("value", [True, "1", object()])
def test_scope_observe_validates_target_types_before_observation(target, value):
    args = build_parser().parse_args(["scope", "observe", "--fetch-waveform"])
    setattr(args, target, value)
    _FakeScopeService.instances = []
    with (
        patch("wavebench.services.agent_observe.load_config") as load,
        patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService),
        pytest.raises(ConfigError, match=target),
    ):
        _run_scope_observe(args)

    load.assert_not_called()
    assert _FakeScopeService.instances == []
