from dataclasses import replace

import pytest

from wavebench.errors import ConfigError
from wavebench.instruments.builtin import BUILTIN_INSTRUMENTS
from wavebench.instruments.capabilities import (
    CAPABILITY_METHODS,
    require_capabilities,
    validate_declared_capabilities,
)


def _scope_descriptor(**changes):
    descriptor = next(
        item for item in BUILTIN_INSTRUMENTS if item.driver_id == "rigol.ds1104"
    )
    return replace(descriptor, **changes)


def test_all_builtin_capabilities_have_runtime_method_mappings():
    for descriptor in BUILTIN_INSTRUMENTS:
        assert set(descriptor.capabilities) <= set(CAPABILITY_METHODS)


def test_source_channel_profile_has_a_runtime_method_mapping():
    assert CAPABILITY_METHODS["source.channel_profile"] == ("get_channel_profile",)


def test_source_coupling_has_runtime_method_mappings():
    assert CAPABILITY_METHODS["source.coupling_profile"] == ("get_coupling_profile",)
    assert CAPABILITY_METHODS["source.coupling_configure"] == ("configure_coupling",)


def test_source_harmonic_has_runtime_method_mappings():
    assert CAPABILITY_METHODS["source.harmonic_profile"] == ("get_harmonic_profile",)
    assert CAPABILITY_METHODS["source.harmonic_configure"] == ("configure_harmonics",)


def test_source_basic_modulation_has_mode_specific_runtime_method_mappings():
    assert CAPABILITY_METHODS["source.modulation_am_profile"] == (
        "get_am_modulation_profile",
    )
    assert CAPABILITY_METHODS["source.modulation_am_configure"] == (
        "configure_am_modulation",
    )
    assert CAPABILITY_METHODS["source.modulation_fm_profile"] == (
        "get_fm_modulation_profile",
    )
    assert CAPABILITY_METHODS["source.modulation_fm_configure"] == (
        "configure_fm_modulation",
    )
    assert CAPABILITY_METHODS["source.modulation_pm_profile"] == (
        "get_pm_modulation_profile",
    )
    assert CAPABILITY_METHODS["source.modulation_pm_configure"] == (
        "configure_pm_modulation",
    )
    assert CAPABILITY_METHODS["source.modulation_pwm_profile"] == (
        "get_pwm_modulation_profile",
    )
    assert CAPABILITY_METHODS["source.modulation_pwm_configure"] == (
        "configure_pwm_modulation",
    )


def test_source_sweep_profile_has_a_runtime_method_mapping():
    assert CAPABILITY_METHODS["source.sweep_profile"] == ("get_sweep_profile",)


def test_source_sweep_control_has_runtime_method_mappings():
    assert CAPABILITY_METHODS["source.sweep_configure"] == ("configure_sweep",)
    assert CAPABILITY_METHODS["source.sweep_trigger"] == ("trigger_sweep",)


def test_source_pulse_and_burst_have_runtime_method_mappings():
    assert CAPABILITY_METHODS["source.pulse_profile"] == ("get_pulse_profile",)
    assert CAPABILITY_METHODS["source.pulse_configure"] == ("configure_pulse",)
    assert CAPABILITY_METHODS["source.burst_profile"] == ("get_burst_profile",)
    assert CAPABILITY_METHODS["source.burst_configure"] == ("configure_burst",)
    assert CAPABILITY_METHODS["source.burst_trigger"] == ("trigger_burst",)


def test_source_counter_profile_has_a_runtime_method_mapping():
    assert CAPABILITY_METHODS["source.counter_profile"] == ("get_counter_profile",)


def test_require_capabilities_names_driver_operation_and_missing_capability():
    descriptor = _scope_descriptor(capabilities=("scope.idn",))

    with pytest.raises(ConfigError) as raised:
        require_capabilities(
            descriptor,
            ("scope.idn", "scope.capture_waveform"),
            operation="scope.capture",
        )

    message = str(raised.value)
    assert "rigol.ds1104" in message
    assert "scope.capture" in message
    assert "scope.capture_waveform" in message


def test_validate_declared_capabilities_checks_only_declared_surface():
    class MinimalDriver:
        def idn(self):
            return "IDN"

        def close(self):
            pass

    validate_declared_capabilities(
        _scope_descriptor(capabilities=("scope.idn",)),
        MinimalDriver(),
    )
