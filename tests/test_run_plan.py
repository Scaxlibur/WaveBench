from pathlib import Path
import tempfile
import unittest

from wavebench.errors import ConfigError
from wavebench.services.run_plan import STEP_SCHEMAS, format_run_plan_schema, load_run_plan


VALID_PLAN = """
[experiment]
name = "dp800_voltage_capture"
label = "dp800_voltage_capture"

[safety]
require_scope_coupling_not = ["DC"]
scope_guard_channel = 2

[restore]
source_state = true
source_channel = 2

[[steps]]
kind = "power.status"
channel = 1

[[steps]]
kind = "scope.capture"
channel = 2
label = "before"
points = "def"
time_range_s = 0.01
save_csv = false

[[steps]]
kind = "power.set"
channel = 1
voltage_v = 3.3
current_limit_a = 0.1
"""


class RunPlanTests(unittest.TestCase):
    def _write_plan(self, content: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "plan.toml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_power_scope_plan_and_safety_guard(self):
        plan = load_run_plan(self._write_plan(VALID_PLAN))
        self.assertEqual(plan.name, "dp800_voltage_capture")
        self.assertEqual(plan.label, "dp800_voltage_capture")
        self.assertEqual(plan.safety.scope_guard_channel, 2)
        self.assertEqual(plan.safety.require_scope_coupling_not, ("DC",))
        self.assertFalse(plan.safety.allow_50ohm)
        self.assertTrue(plan.restore.source_state)
        self.assertEqual(plan.restore.source_channel, 2)
        self.assertEqual(
            [step.kind for step in plan.steps], ["power.status", "scope.capture", "power.set"]
        )
        self.assertEqual(plan.steps[1].fields["points"], "DEF")
        self.assertFalse(plan.steps[1].fields["save_csv"])
        self.assertEqual(plan.steps[2].fields["voltage_v"], 3.3)

    def test_safety_allow_50ohm_requires_boolean(self):
        path = self._write_plan("""
[safety]
allow_50ohm = "yes"

[[steps]]
kind = "scope.capture"
""")
        with self.assertRaisesRegex(ConfigError, "allow_50ohm"):
            load_run_plan(path)

    def test_safety_allow_50ohm_loads_as_explicit_opt_in(self):
        path = self._write_plan("""
[safety]
allow_50ohm = true

[[steps]]
kind = "scope.capture"
""")
        plan = load_run_plan(path)
        self.assertTrue(plan.safety.allow_50ohm)

    def test_unknown_step_kind_is_rejected_with_suggestion(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.captur"
""")
        with self.assertRaisesRegex(ConfigError, "Did you mean 'scope.capture'"):
            load_run_plan(path)

    def test_missing_required_step_field_is_rejected_with_schema_hint(self):
        path = self._write_plan("""
[[steps]]
kind = "power.set"
voltage_v = 3.3
""")
        with self.assertRaisesRegex(ConfigError, "Required fields: voltage_v, current_limit_a"):
            load_run_plan(path)

    def test_power_set_rejects_nonfinite_values(self):
        for field, value in (("voltage_v", "nan"), ("current_limit_a", "inf")):
            with self.subTest(field=field):
                path = self._write_plan(f"""
[[steps]]
kind = "power.set"
channel = 1
voltage_v = {value if field == "voltage_v" else "3.3"}
current_limit_a = {value if field == "current_limit_a" else "0.1"}
""")
                with self.assertRaisesRegex(ConfigError, "must be finite"):
                    load_run_plan(path)


    def test_unknown_step_field_is_rejected_with_suggestion(self):
        path = self._write_plan("""
[[steps]]
kind = "sleep"
duraton_s = 0.5
""")
        with self.assertRaisesRegex(ConfigError, "'duraton_s' -> 'duration_s'"):
            load_run_plan(path)

    def test_step_schemas_drive_schema_output(self):
        self.assertIn("scope.capture", STEP_SCHEMAS)
        self.assertIn("expect", STEP_SCHEMAS["scope.capture"].optional)
        self.assertIn("expect_fft", STEP_SCHEMAS["scope.capture"].optional)

    def test_restore_source_channel_requires_source_state(self):
        path = self._write_plan("""
[restore]
source_channel = 2

[[steps]]
kind = "source.status"
""")
        with self.assertRaises(ConfigError):
            load_run_plan(path)


    def test_restore_source_channels_loads_multiple_channels(self):
        path = self._write_plan("""
[restore]
source_state = true
source_channels = [1, 2]

[[steps]]
kind = "source.status"
""")
        plan = load_run_plan(path)
        self.assertEqual(plan.restore.source_channels, (1, 2))
        self.assertEqual(plan.restore.source_channel, 1)

    def test_restore_source_channels_requires_source_state(self):
        path = self._write_plan("""
[restore]
source_channels = [1, 2]

[[steps]]
kind = "source.status"
""")
        with self.assertRaisesRegex(ConfigError, "restore.source_state"):
            load_run_plan(path)

    def test_restore_source_channel_and_source_channels_are_mutually_exclusive(self):
        path = self._write_plan("""
[restore]
source_state = true
source_channel = 1
source_channels = [1, 2]

[[steps]]
kind = "source.status"
""")
        with self.assertRaisesRegex(ConfigError, "mutually exclusive"):
            load_run_plan(path)

    def test_restore_source_channels_rejects_duplicates(self):
        path = self._write_plan("""
[restore]
source_state = true
source_channels = [1, 1]

[[steps]]
kind = "source.status"
""")
        with self.assertRaisesRegex(ConfigError, "duplicate"):
            load_run_plan(path)

    def test_restore_source_channels_rejects_empty_array(self):
        path = self._write_plan("""
[restore]
source_state = true
source_channels = []

[[steps]]
kind = "source.status"
""")
        with self.assertRaisesRegex(ConfigError, "non-empty array"):
            load_run_plan(path)

    def test_safety_coupling_guard_requires_channel(self):
        path = self._write_plan("""
[safety]
require_scope_coupling_not = ["DC"]

[[steps]]
kind = "power.status"
""")
        with self.assertRaises(ConfigError):
            load_run_plan(path)


    def test_source_arb_load_validates_fields(self):
        path = self._write_plan("""
[[steps]]
kind = "source.arb_load"
channel = 1
file = "waveform.npy"
frequency_hz = 1000
amplitude_vpp = 1.0
offset_v = -0.1
sample_rate_hz = 100000
max_points = 1024
byte_order = "LITTLE"
output_on = true
""")
        plan = load_run_plan(path)
        fields = plan.steps[0].fields
        self.assertEqual(plan.steps[0].kind, "source.arb_load")
        self.assertEqual(fields["frequency_hz"], 1000.0)
        self.assertEqual(fields["amplitude_vpp"], 1.0)
        self.assertEqual(fields["offset_v"], -0.1)
        self.assertEqual(fields["sample_rate_hz"], 100000.0)
        self.assertEqual(fields["max_points"], 1024)
        self.assertEqual(fields["byte_order"], "little")
        self.assertTrue(fields["output_on"])

    def test_source_arb_load_rejects_bad_byte_order_and_non_bool_output(self):
        path = self._write_plan("""
[[steps]]
kind = "source.arb_load"
file = "waveform.npy"
frequency_hz = 1000
amplitude_vpp = 1.0
byte_order = "middle"
""")
        with self.assertRaisesRegex(ConfigError, "byte_order must be"):
            load_run_plan(path)

        path = self._write_plan("""
[[steps]]
kind = "source.arb_load"
file = "waveform.npy"
frequency_hz = 1000
amplitude_vpp = 1.0
output_on = "yes"
""")
        with self.assertRaisesRegex(ConfigError, "output_on must be true or false"):
            load_run_plan(path)

    def test_source_and_sleep_steps_validate_fields(self):
        path = self._write_plan("""
[[steps]]
kind = "source.set_freq"
channel = 2
frequency_hz = 1000

[[steps]]
kind = "source.output"
channel = 2
state = "ON"

[[steps]]
kind = "source.set_duty"
channel = 2
duty_percent = 25

[[steps]]
kind = "sleep"
duration_s = 0.5
""")
        plan = load_run_plan(path)
        self.assertEqual(plan.steps[0].fields["frequency_hz"], 1000.0)
        self.assertEqual(plan.steps[1].fields["state"], "on")
        self.assertEqual(plan.steps[2].fields["duty_percent"], 25.0)
        self.assertEqual(plan.steps[3].fields["duration_s"], 0.5)

    def test_source_v2_steps_validate_explicit_channels_and_closed_basic_patch(self):
        plan = load_run_plan(self._write_plan("""
[[steps]]
kind = "source.basic_configure_v2"
channel = 2
waveform_kind = "SQUARE"
frequency_hz = 1000
amplitude_vpp = 0
offset_v = -0.2
square_duty_cycle_percent = 100

[[steps]]
kind = "source.output_enable_v2"
channel = 2

[[steps]]
kind = "source.output_disable_v2"
channel = 2

[[steps]]
kind = "source.harmonics_configure_v2"
channel = 2
order = 8
preset = "ODD"

[[steps]]
kind = "source.modulation_configure_v2"
channel = 2
depth_percent = 80
internal_frequency_hz = 25

[[steps]]
kind = "source.modulation_pm_configure_v2"
channel = 2
phase_deviation_deg = 90
internal_frequency_hz = 25

[[steps]]
kind = "source.modulation_fm_configure_v2"
channel = 2
frequency_deviation_hz = 12500
internal_frequency_hz = 25

[[steps]]
kind = "source.modulation_pwm_configure_v2"
channel = 2
internal_frequency_hz = 25
duty_deviation_percent = 25

[[steps]]
kind = "source.burst_configure_v2"
channel = 2
cycles = 12
phase_deg = 30
internal_period_s = 0.25
delay_s = 0.5

[[steps]]
kind = "source.pulse_configure_v2"
channel = 2
width_s = 1e-6
delay_s = 0
leading_transition_s = 1e-8
trailing_transition_s = 1e-8
"""))

        basic = plan.steps[0]
        self.assertEqual(basic.fields["channel"], 2)
        self.assertEqual(basic.fields["waveform_kind"], "square")
        self.assertEqual(basic.fields["frequency_hz"], 1000.0)
        self.assertEqual(basic.fields["amplitude_vpp"], 0.0)
        self.assertEqual(basic.fields["offset_v"], -0.2)
        self.assertEqual(basic.fields["square_duty_cycle_percent"], 100.0)
        harmonic = plan.steps[3]
        self.assertEqual(harmonic.fields, {"channel": 2, "order": 8, "preset": "odd"})
        modulation = plan.steps[4]
        self.assertEqual(
            modulation.fields,
            {"channel": 2, "depth_percent": 80.0, "internal_frequency_hz": 25.0},
        )
        pm = plan.steps[5]
        self.assertEqual(
            pm.fields,
            {"channel": 2, "phase_deviation_deg": 90.0, "internal_frequency_hz": 25.0},
        )
        fm = plan.steps[6]
        self.assertEqual(
            fm.fields,
            {
                "channel": 2,
                "frequency_deviation_hz": 12_500.0,
                "internal_frequency_hz": 25.0,
            },
        )
        pwm = plan.steps[7]
        self.assertEqual(
            pwm.fields,
            {
                "channel": 2,
                "internal_frequency_hz": 25.0,
                "duty_deviation_percent": 25.0,
            },
        )
        burst = plan.steps[8]
        self.assertEqual(
            burst.fields,
            {
                "channel": 2,
                "cycles": 12,
                "phase_deg": 30.0,
                "internal_period_s": 0.25,
                "delay_s": 0.5,
            },
        )
        pulse = plan.steps[9]
        self.assertEqual(
            pulse.fields,
            {
                "channel": 2,
                "width_s": 1.0e-6,
                "delay_s": 0.0,
                "leading_transition_s": 1.0e-8,
                "trailing_transition_s": 1.0e-8,
            },
        )

        empty_patch = self._write_plan("""
[[steps]]
kind = "source.basic_configure_v2"
channel = 2
""")
        with self.assertRaisesRegex(ConfigError, "requires at least one basic field"):
            load_run_plan(empty_patch)

        arbitrary = self._write_plan("""
[[steps]]
kind = "source.basic_configure_v2"
channel = 2
waveform_kind = "arbitrary"
""")
        with self.assertRaisesRegex(ConfigError, "waveform_kind must be one of"):
            load_run_plan(arbitrary)

        order_too_low = self._write_plan("""
[[steps]]
kind = "source.harmonics_configure_v2"
channel = 2
order = 1
preset = "odd"
""")
        with self.assertRaisesRegex(ConfigError, "order must be >= 2"):
            load_run_plan(order_too_low)

        fractional_order = self._write_plan("""
[[steps]]
kind = "source.harmonics_configure_v2"
channel = 2
order = 2.5
preset = "odd"
""")
        with self.assertRaisesRegex(ConfigError, "order must be an integer >= 2"):
            load_run_plan(fractional_order)

        unsupported_preset = self._write_plan("""
[[steps]]
kind = "source.harmonics_configure_v2"
channel = 2
order = 8
preset = "user"
""")
        with self.assertRaisesRegex(ConfigError, "preset must be one of"):
            load_run_plan(unsupported_preset)

        excessive_depth = self._write_plan("""
[[steps]]
kind = "source.modulation_configure_v2"
channel = 2
depth_percent = 100.1
internal_frequency_hz = 25
""")
        with self.assertRaisesRegex(ConfigError, "depth_percent must be in"):
            load_run_plan(excessive_depth)

        zero_internal_frequency = self._write_plan("""
[[steps]]
kind = "source.modulation_configure_v2"
channel = 2
depth_percent = 80
internal_frequency_hz = 0
""")
        with self.assertRaisesRegex(ConfigError, "internal_frequency_hz must be > 0"):
            load_run_plan(zero_internal_frequency)

        excessive_pm_deviation = self._write_plan("""
[[steps]]
kind = "source.modulation_pm_configure_v2"
channel = 2
phase_deviation_deg = 360.1
internal_frequency_hz = 25
""")
        with self.assertRaisesRegex(ConfigError, "phase_deviation_deg must be in"):
            load_run_plan(excessive_pm_deviation)

        zero_fm_deviation = self._write_plan("""
[[steps]]
kind = "source.modulation_fm_configure_v2"
channel = 2
frequency_deviation_hz = 0
internal_frequency_hz = 25
""")
        with self.assertRaisesRegex(ConfigError, "frequency_deviation_hz must be > 0"):
            load_run_plan(zero_fm_deviation)

        invalid_pwm_branches = self._write_plan("""
[[steps]]
kind = "source.modulation_pwm_configure_v2"
channel = 2
internal_frequency_hz = 25
duty_deviation_percent = 25
width_deviation_s = 1e-6
""")
        with self.assertRaisesRegex(ConfigError, "exactly one deviation branch"):
            load_run_plan(invalid_pwm_branches)

        invalid_burst_cycles = self._write_plan("""
[[steps]]
kind = "source.burst_configure_v2"
channel = 2
cycles = 0
phase_deg = 30
internal_period_s = 0.25
delay_s = 0.5
""")
        with self.assertRaisesRegex(ConfigError, "cycles must be in"):
            load_run_plan(invalid_burst_cycles)

        short_width = self._write_plan("""
[[steps]]
kind = "source.pulse_configure_v2"
channel = 2
width_s = 3e-9
delay_s = 0
leading_transition_s = 1e-9
trailing_transition_s = 1e-9
""")
        with self.assertRaisesRegex(ConfigError, "width_s must be >="):
            load_run_plan(short_width)

        oversized_transition = self._write_plan("""
[[steps]]
kind = "source.pulse_configure_v2"
channel = 2
width_s = 1e-6
delay_s = 0
leading_transition_s = 7e-7
trailing_transition_s = 1e-8
""")
        with self.assertRaisesRegex(ConfigError, "leading_transition_s must be <="):
            load_run_plan(oversized_transition)


    def test_format_run_plan_schema_lists_expect_and_power_output(self):
        text = format_run_plan_schema()
        self.assertIn("power.output", text)
        self.assertIn("source.arb_load", text)
        self.assertIn("source.basic_configure_v2", text)
        self.assertIn("source.output_enable_v2", text)
        self.assertIn("source.harmonics_configure_v2", text)
        self.assertIn("source.modulation_configure_v2", text)
        self.assertIn("source.modulation_pm_configure_v2", text)
        self.assertIn("source.modulation_fm_configure_v2", text)
        self.assertIn("source.modulation_pwm_configure_v2", text)
        self.assertIn("source.burst_configure_v2", text)
        self.assertIn("source.pulse_configure_v2", text)
        self.assertIn("sweep.frequency_response", text)
        self.assertIn("[steps.expect]", text)
        self.assertIn("[steps.expect_fft]", text)
        self.assertIn("frequency_estimate_hz", text)

    def test_frequency_response_plan_normalizes_log_frequency_points_and_fit(self):
        plan = load_run_plan(self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
source_channel = 2
reference_channel = 1
response_channel = 2
start_frequency_hz = 100
stop_frequency_hz = 10000
frequency_count = 3
spacing = "log"
target_cycles = 8
min_signal_vpp = 0.005
settle_s = 0

[steps.fit]
methods = ["linear_log", "polynomial"]
polynomial_degree = 2
"""))

        fields = plan.steps[0].fields
        self.assertEqual(fields["frequencies_hz"][0], 100.0)
        self.assertAlmostEqual(fields["frequencies_hz"][1], 1000.0)
        self.assertEqual(fields["frequencies_hz"][2], 10000.0)
        self.assertEqual(fields["target_cycles"], 8.0)
        self.assertEqual(fields["min_signal_vpp"], 0.005)
        self.assertEqual(fields["settle_s"], 0.0)
        self.assertTrue(fields["retry_warning_with_autoscale"])
        self.assertEqual(
            fields["fit"],
            {"methods": ["linear_log", "polynomial"], "polynomial_degree": 2},
        )

    def test_frequency_response_plan_generates_vpp_slices_and_calibration(self):
        plan = load_run_plan(self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
source_channel = 1
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000, 10000, 100000]
start_vpp = 0.05
stop_vpp = 0.15
vpp_step = 0.05

[steps.calibration]
target_mode = "explicit_gain_db"
target_gain_db = -1.0
"""))

        fields = plan.steps[0].fields
        self.assertEqual(fields["amplitudes_vpp"], [0.05, 0.1, 0.15])
        self.assertTrue(fields["autoscale_each_amplitude"])
        self.assertEqual(fields["calibration"]["target_gain_db"], -1.0)

    def test_frequency_response_plan_accepts_warning_retry_configuration(self):
        plan = load_run_plan(self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
retry_warning_with_autoscale = false
"""))

        self.assertFalse(plan.steps[0].fields["retry_warning_with_autoscale"])

    def test_frequency_response_plan_rejects_non_positive_min_signal_vpp(self):
        path = self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
min_signal_vpp = 0
""")
        with self.assertRaisesRegex(ConfigError, "min_signal_vpp must be > 0"):
            load_run_plan(path)

    def test_frequency_response_plan_rejects_mixed_vpp_forms(self):
        path = self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
amplitudes_vpp = [0.05, 0.1]
start_vpp = 0.05
stop_vpp = 0.1
vpp_step = 0.05
""")
        with self.assertRaisesRegex(ConfigError, "either amplitudes_vpp"):
            load_run_plan(path)

    def test_frequency_response_plan_rejects_conflicting_channels_and_frequencies(self):
        same_channel = self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 1
frequencies_hz = [100, 1000]
""")
        with self.assertRaisesRegex(ConfigError, "must differ"):
            load_run_plan(same_channel)

        mixed_frequency_forms = self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
start_frequency_hz = 100
stop_frequency_hz = 1000
frequency_count = 2
""")
        with self.assertRaisesRegex(ConfigError, "either frequencies_hz"):
            load_run_plan(mixed_frequency_forms)

    def test_frequency_response_plan_rejects_bad_frequency_lists_and_allows_unique_multiple_steps(self):
        duplicate_frequencies = self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 100]
""")
        with self.assertRaisesRegex(ConfigError, "strictly increasing"):
            load_run_plan(duplicate_frequencies)

        multiple_steps = self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]

[[steps]]
kind = "sweep.frequency_response"
reference_channel = 3
response_channel = 4
frequencies_hz = [100, 1000]
""")
        self.assertEqual(len(load_run_plan(multiple_steps).steps), 2)

    def test_frequency_response_plan_rejects_duplicate_response_labels_and_accepts_adaptive_baseline(self):
        duplicate_labels = self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
label = "same"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]

[[steps]]
kind = "sweep.frequency_response"
label = "same"
reference_channel = 3
response_channel = 4
frequencies_hz = [100, 1000]
""")
        with self.assertRaisesRegex(ConfigError, "labels must be unique"):
            load_run_plan(duplicate_labels)

        plan = load_run_plan(self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]

[steps.baseline]
run_dir = "baseline-run"

[steps.adaptive]
enabled = true
gain_threshold_db = 0.25
phase_threshold_deg = 5
max_levels = 2
max_frequency_points = 20
"""))
        self.assertEqual(plan.steps[0].fields["baseline"]["mode"], "complex_transfer")
        self.assertEqual(plan.steps[0].fields["adaptive"]["max_frequency_points"], 20)

    def test_frequency_response_fit_rejects_unknown_method_and_high_degree(self):
        unknown_method = self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]

[steps.fit]
methods = ["spline"]
""")
        with self.assertRaisesRegex(ConfigError, "linear_log"):
            load_run_plan(unknown_method)

        high_degree = self._write_plan("""
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]

[steps.fit]
polynomial_degree = 6
""")
        with self.assertRaisesRegex(ConfigError, "must be <= 5"):
            load_run_plan(high_degree)

    def test_scope_auto_step_is_explicit_and_has_no_fields(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.auto"
""")
        plan = load_run_plan(path)
        self.assertEqual(plan.steps[0].kind, "scope.auto")
        self.assertEqual(plan.steps[0].fields, {})

    def test_scope_auto_rejects_unknown_fields(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.auto"
channel = 1
""")
        with self.assertRaisesRegex(ConfigError, "unknown key"):
            load_run_plan(path)

    def test_scope_target_cycles_derives_time_range(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
window_frequency_hz = 100000
target_cycles = 10
""")
        plan = load_run_plan(path)
        self.assertAlmostEqual(plan.steps[0].fields["time_range_s"], 0.0001)

    def test_scope_target_cycles_requires_frequency(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
target_cycles = 10
""")
        with self.assertRaisesRegex(ConfigError, "target_cycles requires"):
            load_run_plan(path)

    def test_scope_capture_accepts_quality_gate_and_auto_recover(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
quality_gate = true
auto_recover = true
""")
        plan = load_run_plan(path)
        self.assertTrue(plan.steps[0].fields["quality_gate"])
        self.assertTrue(plan.steps[0].fields["auto_recover"])

    def test_scope_capture_accepts_autoscale_before_capture_fields(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
autoscale_before_capture = true
autoscale_settle_s = 0
""")
        plan = load_run_plan(path)
        self.assertTrue(plan.steps[0].fields["autoscale_before_capture"])
        self.assertEqual(plan.steps[0].fields["autoscale_settle_s"], 0.0)

    def test_scope_capture_rejects_invalid_autoscale_before_capture_fields(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
autoscale_before_capture = "yes"
""")
        with self.assertRaisesRegex(ConfigError, "autoscale_before_capture"):
            load_run_plan(path)

        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
autoscale_settle_s = -0.1
""")
        with self.assertRaisesRegex(ConfigError, "autoscale_settle_s"):
            load_run_plan(path)


    def test_scope_capture_accepts_screenshot_flag(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
label = "screen"
screenshot = true
""")
        plan = load_run_plan(path)
        self.assertTrue(plan.steps[0].fields["screenshot"])
        self.assertIn("screenshot", STEP_SCHEMAS["scope.capture"].optional)

    def test_scope_capture_rejects_non_bool_screenshot_flag(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
screenshot = "yes"
""")
        with self.assertRaisesRegex(ConfigError, "screenshot must be true or false"):
            load_run_plan(path)


    def test_scope_capture_accepts_expect_limits(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
label = "pwm"

[steps.expect]
duty_cycle = { min = 0.49, max = 0.51 }
frequency_error_ratio = { max = 0.02 }
voltage_vpp_v = { min = 3.0, max = 3.6 }
""")
        plan = load_run_plan(path)
        expect = plan.steps[0].fields["expect"]
        self.assertEqual(expect["duty_cycle"], {"min": 0.49, "max": 0.51})
        self.assertEqual(expect["frequency_error_ratio"], {"max": 0.02})

    def test_scope_capture_accepts_fft_expect_limits(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
label = "fft"

[steps.expect_fft]
peak_frequency_hz = { min = 990.0, max = 1010.0 }
harmonic_2_amplitude_v = { max = 0.2 }
""")
        plan = load_run_plan(path)
        expect_fft = plan.steps[0].fields["expect_fft"]
        self.assertEqual(expect_fft["peak_frequency_hz"], {"min": 990.0, "max": 1010.0})
        self.assertEqual(expect_fft["harmonic_2_amplitude_v"], {"max": 0.2})

    def test_scope_capture_expect_requires_limits(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"

[steps.expect.frequency_error_ratio]
""")
        with self.assertRaisesRegex(ConfigError, "requires min or max"):
            load_run_plan(path)

    def test_scope_capture_rejects_non_bool_quality_gate(self):
        path = self._write_plan("""
[[steps]]
kind = "scope.capture"
quality_gate = "yes"
""")
        with self.assertRaisesRegex(ConfigError, "quality_gate must be true or false"):
            load_run_plan(path)


    def test_dmm_read_step_defaults_to_dcv(self):
        path = self._write_plan("""
[[steps]]
kind = "dmm.read"
""")
        plan = load_run_plan(path)
        self.assertEqual(plan.steps[0].kind, "dmm.read")
        self.assertEqual(plan.steps[0].fields["function"], "dcv")

    def test_dmm_read_step_accepts_explicit_function(self):
        path = self._write_plan("""
[[steps]]
kind = "dmm.read"
function = "acv"
""")
        plan = load_run_plan(path)
        self.assertEqual(plan.steps[0].fields["function"], "acv")

    def test_dmm_read_step_accepts_expect_limits(self):
        path = self._write_plan("""
[[steps]]
kind = "dmm.read"
function = "acv"

[steps.expect]
value = { min = 0.34, max = 0.37 }
""")
        plan = load_run_plan(path)
        self.assertEqual(plan.steps[0].fields["expect"]["value"], {"min": 0.34, "max": 0.37})


if __name__ == "__main__":
    unittest.main()
