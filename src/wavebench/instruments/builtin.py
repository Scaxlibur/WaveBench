from __future__ import annotations

from wavebench import __version__

from .api import InstrumentDescriptor


def _open_rtm2032(context):
    from wavebench.drivers.rtm2032 import RTM2032Scope

    return RTM2032Scope(
        transport=context.open_transport(),
        check_errors_after_ops=bool(context.settings["check_errors"]),
    )


def _open_ds1000z(context):
    from wavebench.drivers.ds1104 import DS1104Scope

    return DS1104Scope(
        transport=context.open_transport(),
        check_errors_after_ops=bool(context.settings["check_errors"]),
    )


def _open_dg4202(context):
    from wavebench.drivers.dg4202 import DG4202Source

    return DG4202Source(
        transport=context.open_transport(),
        check_errors_after_ops=bool(context.settings["check_errors"]),
    )


def _open_dp800(context):
    from wavebench.drivers.dp800 import DP800Power

    return DP800Power(
        transport=context.open_transport(),
        check_errors_after_ops=bool(context.settings["check_errors"]),
    )


def _open_dm3000(context):
    from wavebench.drivers.dm3000 import DM3000Dmm

    return DM3000Dmm(transport=context.open_transport())


_INSTRUMENT_IO_PERMISSIONS = ("instrument.io", "configured-resource-only")

BUILTIN_INSTRUMENTS: tuple[InstrumentDescriptor, ...] = (
    InstrumentDescriptor(
        driver_id="rohde-schwarz.rtm2032",
        kind="scope",
        display_name="Rohde & Schwarz RTM2032 Oscilloscope",
        manufacturer="Rohde & Schwarz",
        models=("RTM2032", "RTM2000"),
        aliases=("rtm2032",),
        capabilities=(
            "scope.idn",
            "scope.errors",
            "scope.autoscale",
            "scope.fetch_waveform",
            "scope.capture_waveform",
            "scope.capture_waveforms",
            "scope.screenshot",
            "scope.channel_coupling",
        ),
        idn_patterns=("Rohde&Schwarz,RTM", "Rohde & Schwarz,RTM"),
        backends=("rsinstrument",),
        option_specs=(),
        permissions=_INSTRUMENT_IO_PERMISSIONS,
        factory=_open_rtm2032,
        summary="R&S RTM2000-series scope capture driver used by WaveBench scope services.",
        version=__version__,
        scope_coupling_policy="switchable-termination",
        config_fields=("connection.resource", "scope.driver", "waveform.*"),
    ),
    InstrumentDescriptor(
        driver_id="rigol.ds1104",
        kind="scope",
        display_name="RIGOL DS1104Z/DS1000Z Oscilloscope",
        manufacturer="RIGOL Technologies",
        models=("DS1104Z", "DS1104Z Plus", "DS1104Z-S Plus", "DS1000Z"),
        aliases=("ds1104", "ds1000z"),
        capabilities=(
            "scope.idn",
            "scope.errors",
            "scope.autoscale",
            "scope.fetch_waveform",
            "scope.capture_waveform",
            "scope.capture_waveforms",
            "scope.screenshot",
            "scope.channel_coupling",
        ),
        idn_patterns=("RIGOL TECHNOLOGIES,DS1104", "RIGOL TECHNOLOGIES,DS1"),
        backends=("pyvisa",),
        option_specs=(),
        permissions=_INSTRUMENT_IO_PERMISSIONS,
        factory=_open_ds1000z,
        summary="RIGOL DS1000Z-series scope driver with BYTE waveform conversion and RAW chunking.",
        version=__version__,
        scope_coupling_policy="fixed-high-impedance",
        config_fields=("connection.resource", "scope.driver", "waveform.*"),
    ),
    InstrumentDescriptor(
        driver_id="rigol.dg4202",
        kind="source",
        display_name="RIGOL DG4202 Function/Arbitrary Waveform Generator",
        manufacturer="RIGOL Technologies",
        models=("DG4202", "DG4000"),
        aliases=("dg4202",),
        capabilities=(
            "source.idn",
            "source.errors",
            "source.status",
            "source.set_frequency",
            "source.set_function",
            "source.set_amplitude_vpp",
            "source.set_square_duty_cycle",
            "source.output",
            "source.arbitrary_probe",
            "source.arbitrary_upload",
        ),
        idn_patterns=("RIGOL TECHNOLOGIES,DG4",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=_INSTRUMENT_IO_PERMISSIONS,
        factory=_open_dg4202,
        summary="RIGOL DG4000-series signal source driver for frequency, waveform, output, and ARB flows.",
        version=__version__,
        config_fields=("source.resource", "source.driver", "safety_limits.max_source_vpp"),
    ),
    InstrumentDescriptor(
        driver_id="rigol.dp800",
        kind="power",
        display_name="RIGOL DP800 Power Supply",
        manufacturer="RIGOL Technologies",
        models=("DP800", "DP832", "DP832A"),
        aliases=("dp800",),
        capabilities=(
            "power.idn",
            "power.status",
            "power.measurement",
            "power.set_voltage_current_limit",
            "power.output",
            "power.protection",
        ),
        idn_patterns=("RIGOL TECHNOLOGIES,DP8",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=_INSTRUMENT_IO_PERMISSIONS,
        factory=_open_dp800,
        summary="RIGOL DP800-series programmable power supply driver with explicit safety limits.",
        version=__version__,
        config_fields=(
            "power.resource",
            "power.driver",
            "safety_limits.max_power_voltage_v",
            "safety_limits.max_power_current_limit_a",
        ),
    ),
    InstrumentDescriptor(
        driver_id="rigol.dm3000",
        kind="dmm",
        display_name="RIGOL DM3000/DM3058 Digital Multimeter",
        manufacturer="RIGOL Technologies",
        models=("DM3000", "DM3058"),
        aliases=("dm3000", "dm3058"),
        capabilities=(
            "dmm.idn",
            "dmm.read",
            "dmm.function_status",
            "dmm.set_function",
            "dmm.measurement_profile",
            "dmm.trigger_status",
            "dmm.calculation_status",
            "dmm.calculation_statistics",
            "dmm.system_interface_status",
            "dmm.set_voltage_range",
            "dmm.set_dcv_impedance",
        ),
        idn_patterns=("RIGOL TECHNOLOGIES,DM3", "RIGOL TECHNOLOGIES,DM3058"),
        backends=("serial", "pyvisa"),
        option_specs=(),
        permissions=_INSTRUMENT_IO_PERMISSIONS,
        factory=_open_dm3000,
        summary="RIGOL DM3000-family DMM driver for configured serial or VISA backends.",
        version=__version__,
        config_fields=(
            "dmm.resource",
            "dmm.driver",
            "dmm.backend",
            "dmm.baudrate",
            "dmm.bytesize",
            "dmm.parity",
            "dmm.stopbits",
            "dmm.write_termination",
            "dmm.read_termination",
            "dmm.xonxoff",
            "dmm.rtscts",
            "dmm.dsrdtr",
        ),
    ),
)
