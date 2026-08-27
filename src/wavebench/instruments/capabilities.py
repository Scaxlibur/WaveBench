from __future__ import annotations

from collections.abc import Iterable

from wavebench.errors import ConfigError

from .api import InstrumentDescriptor
from .scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    validate_scope_descriptor,
)
from .source_extension_capabilities import (
    SOURCE_EXTENSION_CAPABILITY_METHODS,
    validate_source_descriptor,
)
from .rf_source_capabilities import (
    RF_SOURCE_CAPABILITY_METHODS,
    validate_rf_source_descriptor,
)


_PROFILE_AWARE_WAVEFORM_CAPABILITIES = frozenset(
    {
        "scope.fetch_waveform",
        "scope.capture_waveform",
        "scope.capture_waveforms",
    }
)


CAPABILITY_METHODS: dict[str, tuple[str, ...]] = {
    "scope.idn": ("idn",),
    "scope.errors": ("errors",),
    "scope.autoscale": ("autoscale",),
    "scope.fetch_waveform": ("fetch_waveform",),
    "scope.capture_waveform": ("capture_waveform",),
    "scope.capture_waveforms": ("capture_waveforms",),
    "scope.screenshot": ("screenshot_png",),
    "scope.channel_coupling": ("channel_coupling",),
    "scope.snapshot": ("get_snapshot",),
    "scope.acquisition_status": ("get_acquisition_status",),
    "scope.capture_average": ("capture_average",),
    "scope.digital_status": ("get_digital_status",),
    "scope.digital_waveform": ("get_digital_waveform",),
    "scope.history_timestamps": ("get_history_timestamps",),
    "scope.measurement_statistics": ("get_measurement_statistics",),
    "scope.math_metadata": ("get_math_waveform_metadata",),
    "scope.fft_status": ("get_fft_status",),
    "scope.reference_metadata": ("get_reference_waveform_metadata",),
    "scope.cursor_readout": ("get_cursor_readout",),
    "source.idn": ("idn",),
    "source.errors": ("errors", "assert_no_errors"),
    "source.status": ("get_status",),
    "source.channel_profile": ("get_channel_profile",),
    "source.coupling_profile": ("get_coupling_profile",),
    "source.coupling_configure": ("configure_coupling",),
    "source.harmonic_profile": ("get_harmonic_profile",),
    "source.harmonic_configure": ("configure_harmonics",),
    "source.modulation_am_profile": ("get_am_modulation_profile",),
    "source.modulation_am_configure": ("configure_am_modulation",),
    "source.modulation_fm_profile": ("get_fm_modulation_profile",),
    "source.modulation_fm_configure": ("configure_fm_modulation",),
    "source.modulation_pm_profile": ("get_pm_modulation_profile",),
    "source.modulation_pm_configure": ("configure_pm_modulation",),
    "source.modulation_pwm_profile": ("get_pwm_modulation_profile",),
    "source.modulation_pwm_configure": ("configure_pwm_modulation",),
    "source.pulse_profile": ("get_pulse_profile",),
    "source.pulse_configure": ("configure_pulse",),
    "source.burst_profile": ("get_burst_profile",),
    "source.burst_configure": ("configure_burst",),
    "source.burst_trigger": ("trigger_burst",),
    "source.sweep_profile": ("get_sweep_profile",),
    "source.sweep_configure": ("configure_sweep",),
    "source.sweep_trigger": ("trigger_sweep",),
    "source.counter_profile": ("get_counter_profile",),
    "source.set_frequency": ("set_frequency",),
    "source.set_function": ("set_function",),
    "source.set_amplitude_vpp": ("set_amplitude_vpp",),
    "source.set_square_duty_cycle": ("set_square_duty_cycle",),
    "source.output": ("set_output",),
    "source.arbitrary_probe": ("probe_arbitrary_queries",),
    "source.arbitrary_upload": ("upload_dg4000_dac14_block",),
    "power.idn": ("idn",),
    "power.status": ("get_status",),
    "power.measurement": ("get_measurement",),
    "power.set_voltage_current_limit": ("set_voltage_current_limit",),
    "power.output": ("set_output",),
    "power.protection": ("get_protection_status", "set_protection"),
    "dmm.idn": ("idn",),
    "dmm.read": ("read",),
    "dmm.function_status": ("function_status",),
    "dmm.set_function": ("set_function",),
    "dmm.measurement_profile": ("measurement_profile",),
    "dmm.trigger_status": ("trigger_status",),
    "dmm.calculation_status": ("calculation_status",),
    "dmm.calculation_statistics": ("calculation_statistics",),
    "dmm.system_interface_status": ("system_interface_status",),
    "dmm.set_voltage_range": ("set_voltage_range",),
    "dmm.set_dcv_impedance": ("set_dcv_impedance",),
    "sweep_analyzer.idn": ("idn",),
    "sweep_analyzer.status": ("get_snapshot",),
    "sweep_analyzer.trace": ("fetch_frequency_response",),
    "sweep_analyzer.configure": ("apply_sweep_plan",),
    "sweep_analyzer.trigger": ("trigger_single",),
    "sweep_analyzer.output": ("set_source_output",),
    "sweep_analyzer.marker": ("read_markers",),
    "sweep_analyzer.analysis": ("read_measurements",),
}
CAPABILITY_METHODS.update(SCOPE_CAPABILITY_METHODS)
CAPABILITY_METHODS.update(SOURCE_EXTENSION_CAPABILITY_METHODS)
CAPABILITY_METHODS.update(RF_SOURCE_CAPABILITY_METHODS)


def require_capabilities(
    descriptor: InstrumentDescriptor,
    required: Iterable[str],
    *,
    operation: str,
) -> None:
    missing = sorted(set(required) - set(descriptor.capabilities))
    if missing:
        raise ConfigError(
            f"instrument driver {descriptor.driver_id!r} cannot perform {operation!r}; "
            f"missing capabilities: {', '.join(missing)}"
        )


def validate_declared_capabilities(
    descriptor: InstrumentDescriptor,
    driver: object,
) -> None:
    if not callable(getattr(driver, "close", None)):
        raise TypeError("factory returned a driver without callable close()")
    extensions = descriptor.scope_extensions
    waveform_binary_profile = (
        extensions.waveform_binary_profile if extensions is not None else None
    )
    for capability in descriptor.capabilities:
        if (
            waveform_binary_profile is not None
            and capability in _PROFILE_AWARE_WAVEFORM_CAPABILITIES
        ):
            continue
        methods = CAPABILITY_METHODS.get(capability)
        if methods is None:
            raise TypeError(f"descriptor declares unknown capability {capability!r}")
        missing_methods = [
            method for method in methods if not callable(getattr(driver, method, None))
        ]
        if missing_methods:
            raise TypeError(
                f"descriptor declares capability {capability!r}, but driver lacks callable "
                f"method(s): {', '.join(missing_methods)}"
            )
    validate_scope_descriptor(descriptor, driver=driver)
    validate_source_descriptor(descriptor, driver=driver)
    validate_rf_source_descriptor(descriptor, driver=driver)
