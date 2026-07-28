import unittest
from types import SimpleNamespace
from unittest.mock import patch

from wavebench.drivers.dg4202 import SourceStatus
from wavebench.errors import ConfigError, DataError
from wavebench.instruments import (
    SourceBurstConfiguration,
    SourceBurstProfile,
    SourceChannelProfile,
    SourceCouplingConfiguration,
    SourceCouplingProfile,
    SourceCounterProfile,
    SourcePulseConfiguration,
    SourcePulseProfile,
    SourceSweepConfiguration,
    SourceSweepProfile,
)
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


def make_sweep_configuration(**overrides):
    values = {
        "enabled": True,
        "start_hz": 100.0,
        "stop_hz": 1000.0,
        "spacing": "LINEAR",
        "steps": 101,
        "sweep_time_s": 1.0,
        "start_hold_s": 0.0,
        "stop_hold_s": 0.0,
        "return_time_s": 0.0,
        "trigger_source": "MANUAL",
        "trigger_slope": "POSITIVE",
        "trigger_out": "OFF",
        "marker_enabled": False,
        "marker_frequency_hz": 550.0,
    }
    values.update(overrides)
    return SourceSweepConfiguration(**values)


def make_sweep_profile(**overrides):
    configuration = make_sweep_configuration(**overrides)
    return SourceSweepProfile(
        channel=2,
        enabled=configuration.enabled,
        start_hz=configuration.effective_start_hz,
        stop_hz=configuration.effective_stop_hz,
        center_hz=(configuration.effective_start_hz + configuration.effective_stop_hz) / 2,
        span_hz=configuration.effective_stop_hz - configuration.effective_start_hz,
        spacing=configuration.spacing,
        steps=configuration.steps,
        sweep_time_s=configuration.sweep_time_s,
        start_hold_s=configuration.start_hold_s,
        stop_hold_s=configuration.stop_hold_s,
        return_time_s=configuration.return_time_s,
        trigger_source=configuration.trigger_source,
        trigger_slope=configuration.trigger_slope,
        trigger_out=configuration.trigger_out,
        marker_enabled=configuration.marker_enabled,
        marker_frequency_hz=configuration.marker_frequency_hz,
    )


def make_pulse_configuration():
    return SourcePulseConfiguration(
        hold="WIDTH",
        width_s=1.0e-6,
        delay_s=0.0,
        leading_transition_s=8.0e-9,
        trailing_transition_s=8.0e-9,
    )


def make_burst_configuration():
    return SourceBurstConfiguration(
        enabled=False,
        mode="TRIGGERED",
        cycles=10,
        phase_deg=0.0,
        internal_period_s=0.01,
        delay_s=0.0,
        gate_polarity="NORMAL",
        trigger_source="MANUAL",
        trigger_slope="POSITIVE",
        trigger_out="OFF",
    )


def make_coupling_configuration(**overrides):
    values = {
        "base_channel": 1,
        "frequency_enabled": True,
        "frequency_deviation_hz": 1_000.0,
        "phase_enabled": True,
        "phase_deviation_deg": 90.0,
        "amplitude_enabled": False,
        "amplitude_deviation_vpp": 2.0,
    }
    values.update(overrides)
    return SourceCouplingConfiguration(**values)


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
    def test_configure_non_manual_sweep_checks_output_and_vpp_then_closes_transport(self):
        events = []
        configuration = make_sweep_configuration(trigger_source="INTERNAL")
        profile = make_sweep_profile(trigger_source="INTERNAL")

        class FakeSource:
            def get_status(self, channel):
                events.append(("get_status", channel))
                return make_status(channel=channel, output="OFF", amplitude=1.0)

            def configure_sweep(self, channel, target, *, check_errors):
                events.append(("configure_sweep", channel, target, check_errors))
                return profile

            def close(self):
                events.append(("close",))

        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="example",
                default_channel=2,
                check_errors=True,
            ),
            safety_limits=SimpleNamespace(max_source_vpp=2.0),
        )
        service = SourceService(config=config, logger=CommandLogger())
        with patch.object(service, "_require") as require, patch.object(
            service, "_open_source", return_value=FakeSource()
        ):
            result = service.configure_sweep(configuration)

        self.assertIs(result, profile)
        require.assert_called_once_with(
            "source.sweep_configure",
            "source.sweep_configure",
            "source.status",
            "source.errors",
        )
        self.assertEqual(
            events,
            [
                ("get_status", 2),
                ("configure_sweep", 2, configuration, True),
                ("close",),
            ],
        )

    def test_configure_manual_sweep_requires_persistent_session_before_transport(self):
        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="example",
                default_channel=1,
                check_errors=False,
            ),
            safety_limits=SimpleNamespace(max_source_vpp=2.0),
        )
        service = SourceService(config=config, logger=CommandLogger())
        with patch.object(service, "_require") as require, patch.object(
            service, "_open_source"
        ) as open_source:
            with self.assertRaisesRegex(ConfigError, "persistent source session"):
                service.configure_sweep(make_sweep_configuration())

        require.assert_called_once_with(
            "source.sweep_configure",
            "source.sweep_configure",
            "source.status",
        )
        open_source.assert_not_called()

    def test_configure_manual_sweep_reuses_persistent_session_without_closing_it(self):
        events = []
        configuration = make_sweep_configuration()
        profile = make_sweep_profile()

        class FakeSource:
            def get_status(self, channel):
                events.append(("get_status", channel))
                return make_status(channel=channel, output="OFF", amplitude=1.0)

            def configure_sweep(self, channel, target, *, check_errors):
                events.append(("configure_sweep", channel, target, check_errors))
                return profile

            def close(self):
                events.append(("close",))

        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="example",
                default_channel=2,
                check_errors=True,
            ),
            safety_limits=SimpleNamespace(max_source_vpp=2.0),
        )
        source = FakeSource()
        service = SourceService(config=config, logger=CommandLogger(), session=source)
        with patch.object(service, "_require"):
            result = service.configure_sweep(configuration)

        self.assertIs(result, profile)
        self.assertEqual(
            events,
            [
                ("get_status", 2),
                ("configure_sweep", 2, configuration, True),
            ],
        )

    def test_configure_sweep_rejects_active_output_without_driver_write(self):
        events = []

        class FakeSource:
            def get_status(self, channel):
                events.append(("get_status", channel))
                return make_status(channel=channel, output="ON", amplitude=1.0)

            def configure_sweep(self, *args, **kwargs):
                events.append(("configure_sweep",))

            def close(self):
                events.append(("close",))

        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="example",
                default_channel=1,
                check_errors=False,
            ),
            safety_limits=SimpleNamespace(max_source_vpp=2.0),
        )
        service = SourceService(config=config, logger=CommandLogger())
        with patch.object(service, "_require"), patch.object(
            service, "_open_source", return_value=FakeSource()
        ):
            with self.assertRaisesRegex(ConfigError, "output OFF"):
                service.configure_sweep(
                    make_sweep_configuration(trigger_source="INTERNAL")
                )

        self.assertEqual(events, [("get_status", 1), ("close",)])

    def test_configure_sweep_enforces_existing_vpp_safety_limit(self):
        class FakeSource:
            def get_status(self, channel):
                return make_status(channel=channel, output="OFF", amplitude=3.0)

            def configure_sweep(self, *args, **kwargs):
                raise AssertionError("unsafe sweep reached driver")

            def close(self):
                pass

        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="example",
                default_channel=1,
                check_errors=False,
            ),
            safety_limits=SimpleNamespace(max_source_vpp=2.0),
        )
        service = SourceService(config=config, logger=CommandLogger())
        with patch.object(service, "_require"), patch.object(
            service, "_open_source", return_value=FakeSource()
        ):
            with self.assertRaisesRegex(ConfigError, "安全上限"):
                service.configure_sweep(
                    make_sweep_configuration(trigger_source="INTERNAL")
                )

    def test_configure_sweep_missing_capability_fails_before_transport(self):
        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="legacy",
                default_channel=1,
                check_errors=False,
            )
        )
        service = SourceService(config=config, logger=CommandLogger())
        service.descriptor = SimpleNamespace(
            driver_id="legacy.source",
            capabilities=("source.status",),
        )

        with patch.object(service, "_open_source") as open_source:
            with self.assertRaisesRegex(ConfigError, "source.sweep_configure"):
                service.configure_sweep(make_sweep_configuration())

        open_source.assert_not_called()

    def test_trigger_sweep_requires_persistent_session_before_transport(self):
        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="example",
                default_channel=2,
                check_errors=True,
            )
        )
        service = SourceService(config=config, logger=CommandLogger())
        with patch.object(service, "_require") as require, patch.object(
            service, "_open_source"
        ) as open_source:
            with self.assertRaisesRegex(ConfigError, "persistent source session"):
                service.trigger_sweep()

        require.assert_called_once_with(
            "source.sweep_trigger", "source.sweep_trigger", "source.errors"
        )
        open_source.assert_not_called()

    def test_trigger_sweep_uses_default_channel_on_persistent_session(self):
        events = []

        class FakeSource:
            def trigger_sweep(self, channel, *, check_errors):
                events.append(("trigger_sweep", channel, check_errors))

            def close(self):
                events.append(("close",))

        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="example",
                default_channel=2,
                check_errors=True,
            )
        )
        source = FakeSource()
        service = SourceService(config=config, logger=CommandLogger(), session=source)
        with patch.object(service, "_require") as require:
            service.trigger_sweep()

        require.assert_called_once_with(
            "source.sweep_trigger", "source.sweep_trigger", "source.errors"
        )
        self.assertEqual(events, [("trigger_sweep", 2, True)])

    def test_trigger_sweep_missing_capability_fails_before_transport(self):
        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="legacy",
                default_channel=1,
                check_errors=False,
            )
        )
        service = SourceService(config=config, logger=CommandLogger())
        service.descriptor = SimpleNamespace(
            driver_id="legacy.source",
            capabilities=("source.status",),
        )

        with patch.object(service, "_open_source") as open_source:
            with self.assertRaisesRegex(ConfigError, "source.sweep_trigger"):
                service.trigger_sweep()

        open_source.assert_not_called()

    def test_counter_profile_closes_transport(self):
        events = []
        profile = SourceCounterProfile(
            enabled=False,
            measurement=None,
            coupling="AC",
            impedance_ohm=1_000_000.0,
            attenuation=1,
            gate_time="USER1",
            high_frequency_rejection_enabled=False,
            trigger_level_v=0.0,
            sensitivity_percent=50.0,
            statistics_enabled=False,
            statistics_display="DIGITAL",
        )

        class FakeSource:
            def get_counter_profile(self):
                events.append(("get_counter_profile",))
                return profile

            def close(self):
                events.append(("close",))

        config = SimpleNamespace(
            source=SimpleNamespace(resource="TCPIP::source::INSTR", driver="example")
        )
        service = SourceService(config=config, logger=CommandLogger())
        with patch.object(service, "_require") as require, patch.object(
            service, "_open_source", return_value=FakeSource()
        ):
            result = service.counter_profile()

        self.assertIs(result, profile)
        require.assert_called_once_with("source.counter_profile", "source.counter_profile")
        self.assertEqual(events, [("get_counter_profile",), ("close",)])

    def test_counter_profile_missing_capability_fails_before_transport(self):
        config = SimpleNamespace(
            source=SimpleNamespace(resource="TCPIP::source::INSTR", driver="legacy")
        )
        service = SourceService(config=config, logger=CommandLogger())
        service.descriptor = SimpleNamespace(
            driver_id="legacy.source",
            capabilities=("source.status",),
        )

        with patch.object(service, "_open_source") as open_source:
            with self.assertRaisesRegex(ConfigError, "source.counter_profile"):
                service.counter_profile()

        open_source.assert_not_called()

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

    def test_coupling_profile_is_global_and_closes_transport(self):
        events = []
        profile = SourceCouplingProfile(**make_coupling_configuration().as_dict())

        class FakeSource:
            def get_coupling_profile(self):
                events.append(("get_coupling_profile",))
                return profile

            def close(self):
                events.append(("close",))

        config = SimpleNamespace(
            source=SimpleNamespace(resource="TCPIP::source::INSTR", driver="example")
        )
        service = SourceService(config=config, logger=CommandLogger())
        with patch.object(service, "_require") as require, patch.object(
            service, "_open_source", return_value=FakeSource()
        ):
            result = service.coupling_profile()

        self.assertIs(result, profile)
        require.assert_called_once_with("source.coupling_profile", "source.coupling_profile")
        self.assertEqual(events, [("get_coupling_profile",), ("close",)])

    def test_configure_coupling_has_no_channel_or_output_precondition(self):
        events = []
        configuration = make_coupling_configuration()
        profile = SourceCouplingProfile(**configuration.as_dict())

        class FakeSource:
            def configure_coupling(self, target, *, check_errors):
                events.append(("configure_coupling", target, check_errors))
                return profile

            def close(self):
                events.append(("close",))

        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="example",
                default_channel=2,
                check_errors=True,
            )
        )
        service = SourceService(config=config, logger=CommandLogger())
        with patch.object(service, "_require") as require, patch.object(
            service, "_open_source", return_value=FakeSource()
        ):
            result = service.configure_coupling(configuration)

        self.assertIs(result, profile)
        require.assert_called_once_with(
            "source.coupling_configure",
            "source.coupling_configure",
            "source.errors",
        )
        self.assertEqual(
            events,
            [("configure_coupling", configuration, True), ("close",)],
        )

    def test_configure_coupling_rejects_wrong_configuration_before_transport(self):
        service = SourceService(config=None, logger=CommandLogger())
        service._open_source = lambda: self.fail("transport must not open")

        with self.assertRaisesRegex(ConfigError, "SourceCouplingConfiguration"):
            service.configure_coupling(object())

    def test_coupling_operations_require_declared_capabilities_before_transport(self):
        config = SimpleNamespace(
            source=SimpleNamespace(
                resource="TCPIP::source::INSTR",
                driver="legacy",
                check_errors=False,
            )
        )
        service = SourceService(config=config, logger=CommandLogger())
        service.descriptor = SimpleNamespace(
            driver_id="legacy.source",
            capabilities=("source.status",),
        )

        with patch.object(service, "_open_source") as open_source:
            with self.assertRaisesRegex(ConfigError, "source.coupling_profile"):
                service.coupling_profile()
            with self.assertRaisesRegex(ConfigError, "source.coupling_configure"):
                service.configure_coupling(make_coupling_configuration())

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

    def test_pulse_profile_is_read_only_and_uses_default_channel(self):
        profile = SourcePulseProfile(
            channel=2,
            hold="WIDTH",
            width_s=1.0e-6,
            duty_cycle_percent=20.0,
            delay_s=0.0,
            leading_transition_s=8.0e-9,
            trailing_transition_s=8.0e-9,
        )
        events = []

        class Driver:
            def get_pulse_profile(self, channel):
                events.append(("get_pulse_profile", channel))
                return profile

            def close(self):
                events.append(("close",))

        service = SourceService(config=None, logger=CommandLogger())
        service._source_config = lambda: SimpleNamespace(default_channel=2)
        service._require = lambda *args: None
        service._open_source = Driver

        self.assertIs(service.pulse_profile(), profile)
        self.assertEqual(events, [("get_pulse_profile", 2), ("close",)])

    def test_configure_pulse_requires_output_off_before_driver_write(self):
        events = []

        class Driver:
            def get_status(self, channel):
                events.append(("get_status", channel))
                return make_status(output="ON")

            def configure_pulse(self, *args, **kwargs):
                events.append(("configure_pulse",))

            def close(self):
                events.append(("close",))

        service = SourceService(config=None, logger=CommandLogger())
        service._source_config = lambda: SimpleNamespace(
            default_channel=2,
            check_errors=True,
        )
        service._require = lambda *args: None
        service._open_source = Driver

        with self.assertRaisesRegex(ConfigError, "output OFF"):
            service.configure_pulse(make_pulse_configuration())
        self.assertNotIn(("configure_pulse",), events)

    def test_manual_burst_configuration_and_trigger_reuse_persistent_session(self):
        configuration = make_burst_configuration()
        profile = SourceBurstProfile(channel=2, **configuration.as_dict())
        events = []

        class Driver:
            def get_status(self, channel):
                events.append(("get_status", channel))
                return make_status(output="OFF")

            def configure_burst(self, channel, target, *, check_errors):
                events.append(("configure_burst", channel, target, check_errors))
                return profile

            def trigger_burst(self, channel, *, check_errors):
                events.append(("trigger_burst", channel, check_errors))

        driver = Driver()
        service = SourceService(config=None, logger=CommandLogger(), session=driver)
        service._source_config = lambda: SimpleNamespace(
            default_channel=2,
            check_errors=True,
        )
        service._require = lambda *args: None

        self.assertIs(service.configure_burst(configuration), profile)
        service.trigger_burst()
        self.assertEqual(events, [
            ("get_status", 2),
            ("configure_burst", 2, configuration, True),
            ("trigger_burst", 2, True),
        ])

    def test_manual_burst_configuration_rejects_temporary_session_before_transport(self):
        service = SourceService(config=None, logger=CommandLogger())
        service._source_config = lambda: SimpleNamespace(
            default_channel=2,
            check_errors=True,
        )
        service._require = lambda *args: None
        service._open_source = lambda: self.fail("transport must not open")

        with self.assertRaisesRegex(ConfigError, "persistent source session"):
            service.configure_burst(make_burst_configuration())


if __name__ == "__main__":
    unittest.main()
