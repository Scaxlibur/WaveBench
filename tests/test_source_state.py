import unittest
from types import SimpleNamespace
from unittest.mock import patch

from wavebench.drivers.dg4202 import SourceStatus
from wavebench.errors import ConfigError, DataError
from wavebench.instruments import SourceChannelProfile, SourceSweepProfile
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.services.source_state import RestorableSourceState


def make_status(**overrides):
    values = {
        "channel": 2,
        "output": "ON",
        "function": "SIN",
        "frequency_hz": 5000.0,
        "amplitude": 5.0,
        "amplitude_unit": "VPP",
        "offset_v": 0.0,
        "phase_deg": 0.0,
        "frequency_mode": "FIX",
        "sweep_enabled": "OFF",
        "apply_raw": None,
        "square_duty_cycle_percent": 50.0,
    }
    values.update(overrides)
    return SourceStatus(**values)


class RestorableSourceStateTests(unittest.TestCase):
    def test_from_status_keeps_only_restorable_fields(self):
        state = RestorableSourceState.from_status(make_status())
        self.assertEqual(state.channel, 2)
        self.assertEqual(state.output, "ON")
        self.assertEqual(state.function, "SIN")
        self.assertEqual(state.frequency_hz, 5000.0)
        self.assertEqual(state.amplitude_vpp, 5.0)
        self.assertEqual(state.amplitude_unit, "VPP")
        self.assertEqual(state.square_duty_cycle_percent, 50.0)
        self.assertNotIn("frequency_mode", state.as_dict())
        self.assertNotIn("sweep_enabled", state.as_dict())

    def test_from_status_rejects_missing_frequency(self):
        with self.assertRaisesRegex(DataError, "frequency_hz is missing"):
            RestorableSourceState.from_status(make_status(frequency_hz=None))

    def test_from_status_rejects_missing_amplitude(self):
        with self.assertRaisesRegex(DataError, "amplitude is missing"):
            RestorableSourceState.from_status(make_status(amplitude=None))

    def test_from_status_rejects_non_vpp_unit(self):
        with self.assertRaisesRegex(DataError, "only VPP amplitude"):
            RestorableSourceState.from_status(make_status(amplitude_unit="VRMS"))


class SourceServiceSnapshotTests(unittest.TestCase):
    def test_sweep_profile_uses_default_channel_and_closes_transport(self):
        events = []
        profile = SourceSweepProfile(
            channel=2,
            enabled=False,
            start_hz=100.0,
            stop_hz=1000.0,
            center_hz=550.0,
            span_hz=900.0,
            spacing="LINEAR",
            steps=101,
            sweep_time_s=1.0,
            start_hold_s=0.0,
            stop_hold_s=0.0,
            return_time_s=0.0,
            trigger_source="INTERNAL",
            trigger_slope="POSITIVE",
            trigger_out="OFF",
            marker_enabled=False,
            marker_frequency_hz=550.0,
        )

        class FakeSource:
            def get_sweep_profile(self, channel):
                events.append(("get_sweep_profile", channel))
                return profile

            def close(self):
                events.append(("close",))

        config = SimpleNamespace(
            source=SimpleNamespace(resource="TCPIP::source::INSTR", driver="example", default_channel=2)
        )
        service = SourceService(config=config, logger=CommandLogger())
        with patch.object(service, "_require") as require, patch.object(
            service, "_open_source", return_value=FakeSource()
        ):
            result = service.sweep_profile()

        self.assertIs(result, profile)
        require.assert_called_once_with("source.sweep_profile", "source.sweep_profile")
        self.assertEqual(events, [("get_sweep_profile", 2), ("close",)])

    def test_sweep_profile_missing_capability_fails_before_transport(self):
        config = SimpleNamespace(
            source=SimpleNamespace(resource="TCPIP::source::INSTR", driver="legacy", default_channel=1)
        )
        service = SourceService(config=config, logger=CommandLogger())
        service.descriptor = SimpleNamespace(
            driver_id="legacy.source",
            capabilities=("source.status",),
        )

        with patch.object(service, "_open_source") as open_source:
            with self.assertRaisesRegex(ConfigError, "source.sweep_profile"):
                service.sweep_profile()

        open_source.assert_not_called()

    def test_channel_profile_uses_default_channel_and_closes_transport(self):
        events = []
        profile = SourceChannelProfile(
            status=make_status(channel=2),
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

        class FakeSource:
            def get_channel_profile(self, channel):
                events.append(("get_channel_profile", channel))
                return profile

            def close(self):
                events.append(("close",))

        config = SimpleNamespace(
            source=SimpleNamespace(resource="TCPIP::source::INSTR", driver="example", default_channel=2)
        )
        service = SourceService(config=config, logger=CommandLogger())
        with patch.object(service, "_require") as require, patch.object(
            service, "_open_source", return_value=FakeSource()
        ):
            result = service.channel_profile()

        self.assertIs(result, profile)
        require.assert_called_once_with("source.channel_profile", "source.channel_profile")
        self.assertEqual(events, [("get_channel_profile", 2), ("close",)])

    def test_channel_profile_missing_capability_fails_before_transport(self):
        config = SimpleNamespace(
            source=SimpleNamespace(resource="TCPIP::source::INSTR", driver="legacy", default_channel=1)
        )
        service = SourceService(config=config, logger=CommandLogger())
        service.descriptor = SimpleNamespace(
            driver_id="legacy.source",
            capabilities=("source.status",),
        )

        with patch.object(service, "_open_source") as open_source:
            with self.assertRaisesRegex(ConfigError, "source.channel_profile"):
                service.channel_profile()

        open_source.assert_not_called()

    def test_snapshot_restorable_state_uses_status(self):
        service = SourceService(config=None, logger=CommandLogger())
        service.status = lambda channel=None: make_status(channel=channel or 2)

        state = service.snapshot_restorable_state(channel=2)

        self.assertEqual(state.as_dict(), {
            "channel": 2,
            "output": "ON",
            "function": "SIN",
            "frequency_hz": 5000.0,
            "amplitude_vpp": 5.0,
            "amplitude_unit": "VPP",
            "square_duty_cycle_percent": 50.0,
        })

    def test_restore_restorable_state_uses_safe_order(self):
        service = SourceService(config=None, logger=CommandLogger())
        calls = []
        final_status = make_status(output="OFF")
        state = RestorableSourceState.from_status(make_status(output="OFF"))

        def set_function(channel, function):
            calls.append(("set_function", channel, function))
            return make_status(function=function)

        def set_amplitude_vpp(channel, value_vpp):
            calls.append(("set_amplitude_vpp", channel, value_vpp))
            return make_status(amplitude=value_vpp)

        def set_frequency(channel, value_hz):
            calls.append(("set_frequency", channel, value_hz))
            return make_status(frequency_hz=value_hz)

        def set_square_duty_cycle(channel, duty_percent):
            calls.append(("set_square_duty_cycle", channel, duty_percent))
            return make_status(square_duty_cycle_percent=duty_percent)

        def set_output(channel, enabled):
            calls.append(("set_output", channel, enabled))
            return final_status

        service.set_function = set_function
        service.set_amplitude_vpp = set_amplitude_vpp
        service.set_frequency = set_frequency
        service.set_square_duty_cycle = set_square_duty_cycle
        service.set_output = set_output

        result = service.restore_restorable_state(state)

        self.assertIs(result, final_status)
        self.assertEqual(calls, [
            ("set_output", 2, False),
            ("set_function", 2, "SIN"),
            ("set_amplitude_vpp", 2, 5.0),
            ("set_frequency", 2, 5000.0),
            ("set_square_duty_cycle", 2, 50.0),
            ("set_output", 2, False),
        ])

    def test_restore_restorable_state_turns_output_on_when_snapshot_was_on(self):
        service = SourceService(config=None, logger=CommandLogger())
        calls = []
        state = RestorableSourceState.from_status(make_status(output="ON"))
        service.set_function = lambda channel, function: calls.append(("set_function", channel, function)) or make_status()
        service.set_amplitude_vpp = lambda channel, value_vpp: calls.append(("set_amplitude_vpp", channel, value_vpp)) or make_status()
        service.set_frequency = lambda channel, value_hz: calls.append(("set_frequency", channel, value_hz)) or make_status()
        service.set_square_duty_cycle = lambda channel, duty_percent: calls.append(("set_square_duty_cycle", channel, duty_percent)) or make_status()
        service.set_output = lambda channel, enabled: calls.append(("set_output", channel, enabled)) or make_status(output="ON")

        service.restore_restorable_state(state)

        self.assertEqual(calls[0], ("set_output", 2, False))
        self.assertEqual(calls[-1], ("set_output", 2, True))


if __name__ == "__main__":
    unittest.main()
