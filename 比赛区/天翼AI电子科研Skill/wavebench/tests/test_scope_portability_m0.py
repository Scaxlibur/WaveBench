from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.errors import ConfigError
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.models import WaveformData, WaveformHeader
from wavebench.instruments.registry import InstrumentRegistry, build_instrument_registry
from wavebench.instruments.scope_extensions import (
    ScopeDescriptorExtensions,
    ScopeWaveformBinaryOperationProfile,
    ScopeWaveformBinaryProfile,
)
from wavebench.logging import CommandLogger
from wavebench.services.capability_explain import explain_operation
from wavebench.services.scope_service import ScopeService


_LEGACY_SCOPE_CAPABILITIES = (
    "scope.idn",
    "scope.errors",
    "scope.autoscale",
    "scope.fetch_waveform",
    "scope.capture_waveform",
    "scope.capture_waveforms",
    "scope.screenshot",
    "scope.channel_coupling",
)
_V2_SCOPE_CAPABILITIES = frozenset(
    {
        "scope.screenshot_profile",
        "scope.screenshot_v2",
        "scope.acquisition_run_state",
        "scope.acquisition_control",
        "scope.trace_metadata",
        "scope.fetch_trace",
        "scope.error_drain_v1",
        "scope.channel_input_state_v2",
        "scope.digital_status_v2",
        "scope.snapshot_v2",
        "scope.acquisition_status_v2",
        "scope.capture_average_v2",
        "scope.measurement_statistics_v2",
        "scope.fft_status_v2",
        "scope.cursor_readout_v2",
        "scope.channel_display_configure_v2",
    }
)


@pytest.mark.parametrize(
    ("canonical", "aliases", "backend", "coupling_policy"),
    (
        ("rohde-schwarz.rtm2032", ("rtm2032",), "rsinstrument", "switchable-termination"),
        ("rigol.ds1104", ("ds1104", "ds1000z"), "pyvisa", "fixed-high-impedance"),
    ),
)
def test_m0_builtin_scope_descriptors_keep_the_legacy_golden_contract(
    canonical: str,
    aliases: tuple[str, ...],
    backend: str,
    coupling_policy: str,
) -> None:
    registry = build_instrument_registry(include_entry_points=False)
    expected = registry.resolve(canonical, expected_kind="scope")

    for reference in (canonical, *aliases):
        descriptor = registry.resolve(reference, expected_kind="scope")

        assert descriptor == expected
        assert descriptor.driver_id == canonical
        assert descriptor.capabilities == _LEGACY_SCOPE_CAPABILITIES
        assert descriptor.backends == (backend,)
        assert descriptor.scope_coupling_policy == coupling_policy
        assert descriptor.scope_extensions is None
        assert descriptor.wavebench_min_version == "0.8.0"
        assert descriptor.wavebench_max_version == "0.9.0"
        assert not (set(descriptor.capabilities) & _V2_SCOPE_CAPABILITIES)

        for operation in ("scope.fetch_waveform", "scope.capture", "scope.capture_multiple"):
            explanation = explain_operation(operation, descriptor=descriptor)
            assert explanation.status == "supported"
            assert explanation.missing_capabilities == ()


class _LegacyRoutingScope:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def idn(self) -> str:
        self.calls.append("idn")
        return "EXAMPLE,SCOPE,SERIAL,FIRMWARE"

    def fetch_waveform(self, *, channel: int, points: str, check_errors: bool) -> WaveformData:
        self.calls.append("fetch_waveform")
        assert channel == 1
        assert points == "DEF"
        assert check_errors is False
        return _waveform(channel)

    def capture_waveform(
        self,
        *,
        channel: int,
        points: str,
        check_errors: bool,
        time_range_s: float | None,
    ) -> WaveformData:
        self.calls.append("capture_waveform")
        assert channel == 1
        assert points == "DEF"
        assert check_errors is False
        assert time_range_s is None
        return _waveform(channel)

    def close(self) -> None:
        pass


@pytest.mark.parametrize("reference", ("rtm2032", "ds1104", "ds1000z"))
def test_m0_legacy_scope_service_routes_standard_waveform_operations_to_legacy_driver(
    tmp_path: Path,
    reference: str,
) -> None:
    descriptor = build_instrument_registry(include_entry_points=False).resolve(
        reference,
        expected_kind="scope",
    )
    driver = _LegacyRoutingScope()
    service = ScopeService(
        config=_scope_config(tmp_path, driver=reference),
        logger=CommandLogger(),
        session=driver,
        descriptor=descriptor,
    )

    assert service._waveform_binary_profile() is None
    assert service.fetch_waveform(1).channel == 1
    assert service.capture_waveform(channel=1, label="m0-legacy").waveform.channel == 1
    assert driver.calls == ["fetch_waveform", "idn", "capture_waveform"]


class _ExternalEntryPoint:
    group = "wavebench.instruments"

    def __init__(self, name: str, descriptor: InstrumentDescriptor) -> None:
        self.name = name
        self.descriptor = descriptor
        self.load_count = 0
        self.dist = None

    def load(self) -> InstrumentDescriptor:
        self.load_count += 1
        return self.descriptor


def _external_scope_descriptor(*, bounded: bool, factory_calls: list[str]) -> InstrumentDescriptor:
    profile = _bounded_profile() if bounded else None
    capabilities = (
        ("scope.idn", "scope.fetch_waveform") if bounded else ("scope.idn", "scope.fetch_waveform")
    )

    def factory(_context: object) -> object:
        factory_calls.append("factory")
        raise AssertionError("descriptor resolution must not instantiate a driver")

    return InstrumentDescriptor(
        driver_id="example.scope-plugin",
        kind="scope",
        display_name="Example external scope",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities,
        idn_patterns=("EXAMPLE,EX1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=factory,
        distribution="wavebench-example-scope",
        wavebench_min_version="0.8.24" if bounded else "0.8.0",
        wavebench_max_version="0.9.0",
        scope_extensions=(
            ScopeDescriptorExtensions(waveform_binary_profile=profile) if profile is not None else None
        ),
    )


def _bounded_profile() -> ScopeWaveformBinaryProfile:
    return ScopeWaveformBinaryProfile(
        operations=(
            ScopeWaveformBinaryOperationProfile(
                operation_kind="fetch",
                response_max_bytes=1_024,
                operation_max_bytes=4_096,
                query_max_count=4,
                resynchronization_max_bytes=0,
                restore_order=("scope.waveform_source",),
                snapshot_max_steps=1,
                restore_max_steps=1,
                verify_max_steps=1,
            ),
        )
    )


@pytest.mark.parametrize(
    ("core_version", "bounded_plugin", "loads"),
    (
        ("0.8.23", False, True),
        ("0.8.24", False, True),
        ("0.8.23", True, False),
        ("0.8.24", True, True),
    ),
    ids=("old-core-old-plugin", "new-core-old-plugin", "old-core-new-plugin", "new-core-new-plugin"),
)
def test_m0_core_plugin_compatibility_matrix_is_resolved_before_factory_or_io(
    monkeypatch: pytest.MonkeyPatch,
    core_version: str,
    bounded_plugin: bool,
    loads: bool,
) -> None:
    factory_calls: list[str] = []
    descriptor = _external_scope_descriptor(
        bounded=bounded_plugin,
        factory_calls=factory_calls,
    )
    entry_point = _ExternalEntryPoint(descriptor.driver_id, descriptor)
    registry = InstrumentRegistry(builtins=(), external_entry_points=(entry_point,))
    monkeypatch.setattr("wavebench.instruments.registry.__version__", core_version)

    if not loads:
        with pytest.raises(ConfigError, match="supports WaveBench >=0.8.24, <0.9.0"):
            registry.resolve(descriptor.driver_id, expected_kind="scope")
        assert entry_point.load_count == 1
        assert factory_calls == []
        return

    resolved = registry.resolve(descriptor.driver_id, expected_kind="scope")

    assert entry_point.load_count == 1
    assert factory_calls == []
    assert resolved.scope_extensions is not None if bounded_plugin else resolved.scope_extensions is None
    if bounded_plugin:
        assert resolved.capabilities == ("scope.idn", "scope.fetch_waveform")
        assert resolved.scope_extensions.waveform_binary_profile is not None
        assert {
            "scope.error_drain_v1",
            "scope.screenshot_v2",
            "scope.capture_waveform",
            "scope.capture_waveforms",
        }.isdisjoint(resolved.capabilities)
    else:
        assert resolved.scope_extensions is None


def _waveform(channel: int) -> WaveformData:
    return WaveformData(
        channel=channel,
        header=WaveformHeader(x_start=0.0, x_stop=0.002, points=3, segment=1),
        voltages_v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
    )


def _scope_config(tmp_path: Path, *, driver: str) -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig(
            backend="lan",
            resource="TCPIP::example::INSTR",
            timeout_ms=1_000,
            opc_timeout_ms=2_000,
        ),
        scope=ScopeConfig(
            driver=driver,
            model_hint=None,
            default_channel=1,
            reset_before_run=False,
            check_errors=False,
        ),
        autoscale=AutoscaleConfig(wait_opc=True, check_errors=False),
        waveform=WaveformConfig(format="real", byte_order="lsbf", points="DEF"),
        output=OutputConfig(
            directory=tmp_path,
            package_naming="timestamp_label",
            save_csv=False,
            save_npy=False,
            save_json=False,
            save_commands_log=False,
            save_screenshot=False,
        ),
        source_path=tmp_path / "wavebench.toml",
    )
