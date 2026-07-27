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


def test_source_sweep_profile_has_a_runtime_method_mapping():
    assert CAPABILITY_METHODS["source.sweep_profile"] == ("get_sweep_profile",)


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
