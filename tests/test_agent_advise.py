from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from wavebench.errors import ConfigError
from wavebench.services.agent_advise import scope_advise_payload


def _write_config(root: Path) -> Path:
    path = root / "wavebench.toml"
    path.write_text(
        """
[connection]
resource = "TCPIP::scope::INSTR"

[scope]
driver = "ds1104"
default_channel = 1
""",
        encoding="utf-8",
    )
    return path


class _NoWaveformFakeScopeService:
    def __init__(self, *, config, logger):
        self.config = config

    def idn(self):
        return "RIGOL TECHNOLOGIES,DS1104Z Plus,123,1.0"

    def require_high_impedance(self, channel, *, allow_50ohm=False):
        return "DC"


def _observation(*, fetch_waveform: bool = True) -> dict:
    return {
        "status": "ok",
        "read_only": True,
        "mutates_instrument": fetch_waveform,
        "raw_scpi": False,
        "instrument_state_effects": ["waveform transfer source/mode/format may be changed"]
        if fetch_waveform
        else [],
        "observation": {
            "channel": 1,
            "channels": [1, 2],
            "fetch_waveform": fetch_waveform,
        },
        "channels": [
            {
                "channel": 1,
                "scope_status": {
                    "status": "ok",
                    "data": {
                        "channel": {"enabled": True, "scale_v_per_div": 1.0},
                    },
                },
                "waveform": {
                    "status": "ok",
                    "data": {
                        "summary": {
                            "frequency_estimate_hz": 1000.0,
                            "estimated_cycles": 2.4,
                            "points_per_cycle": 500.0,
                            "voltage_vpp_v": 1.0,
                            "quality_warnings": ["low_cycle_count: 2.4"],
                        }
                    },
                },
            },
            {
                "channel": 2,
                "scope_status": {
                    "status": "ok",
                    "data": {
                        "channel": {"enabled": True, "scale_v_per_div": 1.0},
                    },
                },
                "waveform": {
                    "status": "ok",
                    "data": {
                        "summary": {
                            "frequency_estimate_hz": 50000.0,
                            "estimated_cycles": 120.0,
                            "points_per_cycle": 10.0,
                            "voltage_vpp_v": 1.0,
                            "quality_warnings": [],
                        }
                    },
                },
            },
        ],
        "relationships": [],
        "warnings": [],
        "agent_hints": [],
    }


def test_scope_advise_recommends_per_channel_focus_and_separate_timebases():
    with TemporaryDirectory() as tmp:
        config = Path(tmp) / "wavebench.toml"
        config.write_text("[scope]\n", encoding="utf-8")
        with patch(
            "wavebench.services.agent_advise.scope_observe_payload",
            return_value=_observation(),
        ):
            payload = scope_advise_payload(
                config_path=config,
                channels=(1, 2),
                fetch_waveform=True,
            )

    assert payload["read_only"] is True
    assert payload["mutates_instrument"] is True
    assert payload["applies_recommendations"] is False
    focus = [item for item in payload["recommendations"] if item["id"] == "focus_channel"]
    assert [item["channel"] for item in focus] == [1, 2]
    assert focus[0]["parameters"]["time_range_s"] == pytest.approx(0.01)
    assert focus[0]["parameters"]["vertical_scale_v_per_div"] == pytest.approx(0.2)
    assert focus[1]["parameters"]["time_range_s"] == pytest.approx(0.0002)
    span = payload["recommendations"][-1]
    assert span["id"] == "separate_timebase_profiles"
    assert span["frequency_span"]["ratio_high_over_low"] == pytest.approx(50.0)
    assert "not applied" in payload["agent_hints"][-1]


def test_scope_advise_can_use_expected_frequency_without_waveform_fetch():
    observation = _observation(fetch_waveform=False)
    for channel in observation["channels"]:
        channel["waveform"] = {"status": "skipped", "reason": "fetch_waveform=false"}
    with TemporaryDirectory() as tmp:
        config = Path(tmp) / "wavebench.toml"
        config.write_text("[scope]\n", encoding="utf-8")
        with patch(
            "wavebench.services.agent_advise.scope_observe_payload",
            return_value=observation,
        ) as observe:
            payload = scope_advise_payload(
                config_path=config,
                channels=(1, 2),
                fetch_waveform=False,
                expectations={
                    1: {"frequency_hz": 1000.0, "vpp_v": 1.0},
                    2: {"frequency_hz": 50000.0, "vpp_v": 1.0},
                },
            )

    observe.assert_called_once()
    assert observe.call_args.kwargs["expectations"] is None
    assert payload["mutates_instrument"] is False
    focus = [item for item in payload["recommendations"] if item["id"] == "focus_channel"]
    assert focus[0]["parameters"]["time_range_s"] == pytest.approx(0.01)
    assert focus[1]["parameters"]["time_range_s"] == pytest.approx(0.0002)
    assert payload["recommendations"][-1]["id"] == "separate_timebase_profiles"


def test_scope_advise_expected_frequency_without_fetch_uses_real_observe_path():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch(
            "wavebench.services.agent_observe.ScopeService",
            _NoWaveformFakeScopeService,
        ):
            payload = scope_advise_payload(
                config_path=config,
                channels=(1, 2),
                fetch_waveform=False,
                expectations={
                    1: {"frequency_hz": 1000.0},
                    2: {"frequency_hz": 50000.0},
                },
            )

    assert payload["mutates_instrument"] is False
    focus = [item for item in payload["recommendations"] if item["id"] == "focus_channel"]
    assert [item["channel"] for item in focus] == [1, 2]
    assert focus[0]["parameters"]["time_range_s"] == pytest.approx(0.01)
    assert focus[1]["parameters"]["time_range_s"] == pytest.approx(0.0002)
    assert payload["recommendations"][-1]["id"] == "separate_timebase_profiles"


def test_scope_advise_rejects_invalid_targets():
    with pytest.raises(ConfigError, match="target_cycles"):
        scope_advise_payload(config_path="wavebench.toml", target_cycles=0)
