import unittest

from wavebench.drivers.dg4202 import SourceStatus
from wavebench.errors import DataError
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
