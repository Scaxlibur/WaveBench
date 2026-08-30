from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    SafetyLimitsConfig,
    ScopeConfig,
    SourceConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.errors import ConfigError
from wavebench.instruments.capabilities import validate_declared_capabilities
from wavebench.instruments.models import SourceStatus
from wavebench.instruments.source_extension_capabilities import validate_source_descriptor
from wavebench.instruments.source_extensions import (
    SOURCE_CONTRACT_VERSION,
    Observed,
    SourceAmplitude,
    SourceAmplitudeUnit,
    SourceBasicConfigureRequest,
    SourceBasicConfigureResult,
    SourceBasicLiveConfigureResult,
    SourceBasicPatch,
    SourceFeatureDirection,
    SourceFieldId,
    SourceOutputRequest,
    SourceOutputResult,
    SourceProtocolQueryRecord,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceRuntimeIdentity,
    SourceTypedObservation,
    SourceV1WriteRouteId,
    SourceWaveformKind,
    PatchAction,
    PatchValue,
)
from wavebench.instruments import (
    SourceAmModulationConfiguration,
    SourceBurstConfiguration,
    SourceCouplingConfiguration,
    SourceFmModulationConfiguration,
    SourceHarmonicConfiguration,
    SourcePmModulationConfiguration,
    SourcePulseConfiguration,
    SourcePwmModulationConfiguration,
    SourceSweepConfiguration,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.services.source_state import RestorableSourceState
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState, SessionHealth

from tests.source_v2_fixtures import (
    basic_facet,
    output_facet,
    source_descriptor,
    source_extensions,
)


class _TextTransport:
    resource = "fake-source-v2"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.writes: list[str] = []

    def record_event(self, direction: str, text: str) -> None:
        del direction, text

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        del replay
        self.queries.append(command)
        return "ok"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def close(self) -> None:
        pass


class _BasicWriteDriver:
    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        combined: bool,
        output_enabled: bool = False,
        postcondition_frequency_hz: float | None = None,
        raise_after_write: bool = False,
    ) -> None:
        self.transport = GuardedAuditedTransport(
            _TextTransport(),
            session_state=session_state,
        )
        self.combined = combined
        self.output_enabled = output_enabled
        self.postcondition_frequency_hz = postcondition_frequency_hz
        self.raise_after_write = raise_after_write
        self.basic = basic_facet()
        self.basic_requests: list[SourceBasicConfigureRequest] = []
        self.live_basic_requests: list[SourceBasicConfigureRequest] = []
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_output_calls = 0
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self.transport.close()

    def execute_source_query_plan_v2(self, plan) -> SourceQueryExecutionRecord:
        records = []
        for index, item in enumerate(plan.items):
            if not self.combined or index == 0:
                self.transport.query("SOURCE:STATE?")
            observations = []
            for field in item.fields:
                if field.field is SourceFieldId.IDENTITY:
                    value = SourceRuntimeIdentity(
                        manufacturer="Example",
                        model="EX1",
                        firmware_id="1.0",
                    )
                elif field.field is SourceFieldId.BASIC:
                    value = self._readback_basic()
                elif field.field is SourceFieldId.OUTPUT:
                    value = output_facet(enabled=self.output_enabled)
                else:  # pragma: no cover - the fixture descriptor only needs these fields.
                    raise AssertionError(field)
                observations.append(SourceTypedObservation(field, value))
            records.append(
                SourceProtocolQueryRecord(
                    item_id=item.item_id,
                    effect=item.effect,
                    outcome=SourceQueryItemOutcome.OBSERVED,
                    query_count=(1 if not self.combined or index == 0 else 0),
                    observations=tuple(observations),
                )
            )
        return SourceQueryExecutionRecord(
            contract_version=SOURCE_CONTRACT_VERSION,
            plan_id=plan.plan_id,
            items=tuple(records),
            query_count=(1 if self.combined else len(records)),
            device_revision_token_before="revision-1",
            device_revision_token_after="revision-1",
        )

    def configure_source_basic_v2(
        self,
        request: SourceBasicConfigureRequest,
    ) -> SourceBasicConfigureResult:
        self.transport.write("SOURCE:CONFIGURE")
        self.basic_requests.append(request)
        self.basic = self._apply_patch(request)
        if self.raise_after_write:
            raise ConfigError("fake basic configure failed after write")
        return SourceBasicConfigureResult(
            channel=request.channel,
            basic=self.basic,
            output_enabled=False,
        )

    def configure_source_basic_live_v2(
        self,
        request: SourceBasicConfigureRequest,
    ) -> SourceBasicLiveConfigureResult:
        self.transport.write("SOURCE:LIVE CONFIGURE")
        self.live_basic_requests.append(request)
        self.basic = self._apply_patch(request)
        if self.raise_after_write:
            raise ConfigError("fake live basic configure failed after write")
        return SourceBasicLiveConfigureResult(
            channel=request.channel,
            basic=self.basic,
            output_enabled=True,
        )

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT OFF")
        self.output_requests.append(request)
        self.output_enabled = request.enabled
        if not request.enabled:
            return SourceOutputResult(channel=request.channel, enabled=False)
        return SourceOutputResult(
            channel=request.channel,
            enabled=True,
            final_amplitude=self.basic.amplitude.value,
            final_offset_v=self.basic.offset_v.value,
        )

    def set_output(self, *args, **kwargs):
        del args, kwargs
        self.v1_output_calls += 1
        raise AssertionError("M5-B recovery must not fall back to the V1 output route")

    def _readback_basic(self):
        if self.postcondition_frequency_hz is None or not (
            self.basic_requests or self.live_basic_requests
        ):
            return self.basic
        return replace(
            self.basic,
            frequency_hz=Observed.value_of(self.postcondition_frequency_hz),
        )

    def _apply_patch(self, request: SourceBasicConfigureRequest):
        patch = request.patch
        updates = {}
        if patch.waveform_kind.action is PatchAction.SET:
            updates["waveform_kind"] = Observed.value_of(patch.waveform_kind.value)
        if patch.frequency_hz.action is PatchAction.SET:
            updates["frequency_hz"] = Observed.value_of(patch.frequency_hz.value)
        if patch.amplitude_vpp.action is PatchAction.SET:
            updates["amplitude"] = Observed.value_of(
                SourceAmplitude(patch.amplitude_vpp.value, SourceAmplitudeUnit.VPP)
            )
        if patch.offset_v.action is PatchAction.SET:
            updates["offset_v"] = Observed.value_of(patch.offset_v.value)
        if patch.square_duty_cycle_percent.action is PatchAction.SET:
            updates["square_duty_cycle_percent"] = Observed.value_of(
                patch.square_duty_cycle_percent.value
            )
        return replace(self.basic, **updates)


class _DualContractDriver(_BasicWriteDriver):
    """V2 basic/output fake with explicit markers for retained V1-only routes."""

    _V1_ADVANCED_METHODS = frozenset(
        {
            "configure_coupling",
            "configure_harmonics",
            "configure_am_modulation",
            "configure_fm_modulation",
            "configure_pm_modulation",
            "configure_pwm_modulation",
            "configure_pulse",
            "configure_burst",
            "configure_sweep",
        }
    )
    _V1_DIRECT_WRITE_METHODS = frozenset(
        {
            "set_frequency",
            "set_function",
            "set_amplitude_vpp",
            "set_square_duty_cycle",
            "trigger_burst",
            "trigger_sweep",
            "upload_dg4000_dac14_block",
        }
    )

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.legacy_calls: list[str] = []

    def get_status(self, channel: int) -> object:
        del channel
        return type("Status", (), {"output": "OFF", "amplitude": 1.0, "amplitude_unit": "VPP"})()

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT OFF")
        self.output_requests.append(request)
        self.output_enabled = request.enabled
        if not request.enabled:
            return SourceOutputResult(channel=request.channel, enabled=False)
        return SourceOutputResult(
            channel=request.channel,
            enabled=True,
            final_amplitude=self.basic.amplitude.value,
            final_offset_v=self.basic.offset_v.value,
        )

    def __getattr__(self, name: str):
        if name in self._V1_ADVANCED_METHODS:
            def retained_v1_route(*args: object, **kwargs: object) -> object:
                del args, kwargs
                self.legacy_calls.append(name)
                return object()

            return retained_v1_route
        if name in self._V1_DIRECT_WRITE_METHODS:
            def forbidden_direct_route(*args: object, **kwargs: object) -> object:
                del args, kwargs
                raise AssertionError(f"dual-contract V1 route bypassed its Source V2 guard: {name}")

            return forbidden_direct_route
        raise AttributeError(name)


class _LegacyWaveformFallbackDriver(_BasicWriteDriver):
    """V1 function support retained outside a narrower V2 basic profile."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.v1_function_requests: list[tuple[int, str, bool]] = []

    def set_function(
        self,
        channel: int,
        function: str,
        *,
        check_errors: bool,
    ) -> SourceStatus:
        self.v1_function_requests.append((channel, function, check_errors))
        self.transport.write("SOURCE:LEGACY FUNCTION")
        normalized = function.strip().upper()
        result_function = {"NOISE": "NOIS", "DC": "DC"}[normalized]
        return SourceStatus(
            channel=channel,
            output="OFF",
            function=result_function,
            frequency_hz=None,
            amplitude=None,
            amplitude_unit=None,
            offset_v=None,
            phase_deg=None,
            frequency_mode="FIX",
            sweep_enabled="OFF",
            apply_raw="SOURCE:LEGACY FUNCTION",
            square_duty_cycle_percent=None,
        )


def _config(*, limits: SafetyLimitsConfig = SafetyLimitsConfig()) -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "DMAX"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("wavebench.toml"),
        source=SourceConfig(
            "example.source-v2",
            "TCPIP::source::INSTR",
            1,
            True,
            True,
            0,
        ),
        safety_limits=limits,
    )


def _write_extensions(
    *,
    include_output: bool,
    live_frequency: bool = False,
    live_amplitude_vpp: bool = False,
    waveform_kinds: tuple[SourceWaveformKind, ...] = (SourceWaveformKind.SINE,),
    square_duty_readable: bool = False,
):
    extensions = source_extensions()
    basic, output = extensions.features
    return replace(
        extensions,
        features=(
            replace(
                basic,
                directions=(
                    SourceFeatureDirection.CONFIGURE,
                    SourceFeatureDirection.READ,
                ),
                profile=replace(
                    basic.profile,
                    live_frequency_configurable=live_frequency,
                    live_amplitude_vpp_configurable=live_amplitude_vpp,
                    waveform_kinds=waveform_kinds,
                    square_duty_readable=square_duty_readable,
                ),
            ),
            replace(
                output,
                directions=(
                    (
                        SourceFeatureDirection.DISABLE,
                        SourceFeatureDirection.ENABLE,
                        SourceFeatureDirection.READ,
                    )
                    if include_output
                    else (SourceFeatureDirection.READ,)
                ),
            ),
        ),
    )


def _service(
    *,
    combined: bool = True,
    include_output: bool = True,
    include_live: bool = False,
    live_frequency: bool = True,
    live_amplitude_vpp: bool = True,
    output_enabled: bool = False,
    postcondition_frequency_hz: float | None = None,
    raise_after_write: bool = False,
    limits: SafetyLimitsConfig = SafetyLimitsConfig(),
    waveform_kinds: tuple[SourceWaveformKind, ...] = (SourceWaveformKind.SINE,),
    square_duty_readable: bool = False,
) -> tuple[SourceService, _BasicWriteDriver]:
    session_state = InstrumentSessionState(epoch_id="source-basic-v2")
    driver = _BasicWriteDriver(
        session_state=session_state,
        combined=combined,
        output_enabled=output_enabled,
        postcondition_frequency_hz=postcondition_frequency_hz,
        raise_after_write=raise_after_write,
    )

    extensions = _write_extensions(
        include_output=include_output,
        live_frequency=(include_live and live_frequency),
        live_amplitude_vpp=(include_live and live_amplitude_vpp),
        waveform_kinds=waveform_kinds,
        square_duty_readable=square_duty_readable,
    )
    capabilities = ["source.snapshot_v2", "source.basic_configure_v2"]
    if include_output:
        capabilities.append("source.output_v2")
    if include_live:
        capabilities.append("source.basic_live_configure_v2")
    descriptor = replace(
        source_descriptor(driver=driver, extensions=extensions),
        capabilities=tuple(capabilities),
    )
    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, driver)
    return (
        SourceService(
            config=_config(limits=limits),
            logger=CommandLogger(),
            session=driver,  # type: ignore[arg-type]
            descriptor=descriptor,
            transport=driver.transport,
            session_state=session_state,
        ),
        driver,
    )


def _legacy_waveform_fallback_service() -> tuple[SourceService, _LegacyWaveformFallbackDriver]:
    session_state = InstrumentSessionState(epoch_id="source-v1-waveform-fallback")
    driver = _LegacyWaveformFallbackDriver(
        session_state=session_state,
        combined=True,
    )
    descriptor = replace(
        source_descriptor(driver=driver, extensions=_write_extensions(include_output=True)),
        capabilities=(
            "source.snapshot_v2",
            "source.basic_configure_v2",
            "source.output_v2",
            "source.set_function",
        ),
    )
    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, driver)
    return (
        SourceService(
            config=_config(),
            logger=CommandLogger(),
            session=driver,  # type: ignore[arg-type]
            descriptor=descriptor,
            transport=driver.transport,
            session_state=session_state,
        ),
        driver,
    )


_DUAL_CONTRACT_V1_CAPABILITIES = (
    "source.status",
    "source.set_frequency",
    "source.set_function",
    "source.set_amplitude_vpp",
    "source.set_square_duty_cycle",
    "source.output",
    "source.coupling_configure",
    "source.harmonic_configure",
    "source.modulation_am_configure",
    "source.modulation_fm_configure",
    "source.modulation_pm_configure",
    "source.modulation_pwm_configure",
    "source.pulse_configure",
    "source.burst_configure",
    "source.burst_trigger",
    "source.sweep_configure",
    "source.sweep_trigger",
    "source.arbitrary_upload",
)


def _dual_contract_service() -> tuple[SourceService, _DualContractDriver]:
    session_state = InstrumentSessionState(epoch_id="source-dual-contract")
    driver = _DualContractDriver(session_state=session_state, combined=True)
    descriptor = replace(
        source_descriptor(driver=driver, extensions=_write_extensions(include_output=True)),
        capabilities=(
            "source.snapshot_v2",
            "source.basic_configure_v2",
            "source.output_v2",
            *_DUAL_CONTRACT_V1_CAPABILITIES,
        ),
    )
    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, driver)
    config = _config()
    assert config.source is not None
    config = replace(config, source=replace(config.source, check_errors=False))
    return (
        SourceService(
            config=config,
            logger=CommandLogger(),
            session=driver,  # type: ignore[arg-type]
            descriptor=descriptor,
            transport=driver.transport,
            session_state=session_state,
        ),
        driver,
    )


def _frequency_request(value_hz: float = 2_000.0) -> SourceBasicConfigureRequest:
    return SourceBasicConfigureRequest(
        channel=1,
        patch=SourceBasicPatch(
            frequency_hz=PatchValue(PatchAction.SET, value_hz),
        ),
    )


def test_basic_live_capability_requires_off_basic_and_output_transactions() -> None:
    driver = _BasicWriteDriver(
        session_state=InstrumentSessionState(epoch_id="source-live-dependencies"),
        combined=True,
    )
    descriptor = replace(
        source_descriptor(
            driver=driver,
            extensions=_write_extensions(
                include_output=False,
                live_frequency=True,
            ),
        ),
        capabilities=("source.snapshot_v2", "source.basic_live_configure_v2"),
    )

    with pytest.raises(ConfigError, match="requires source.basic_configure_v2"):
        validate_source_descriptor(descriptor)


def test_basic_live_capability_requires_explicit_per_field_profile() -> None:
    driver = _BasicWriteDriver(
        session_state=InstrumentSessionState(epoch_id="source-live-profile"),
        combined=True,
    )
    descriptor = replace(
        source_descriptor(
            driver=driver,
            extensions=_write_extensions(include_output=True),
        ),
        capabilities=(
            "source.snapshot_v2",
            "source.basic_configure_v2",
            "source.output_v2",
            "source.basic_live_configure_v2",
        ),
    )

    with pytest.raises(ConfigError, match="per-channel fixed-mode live"):
        validate_source_descriptor(descriptor)


@pytest.mark.parametrize("combined", (True, False))
def test_basic_configure_v2_public_service_supports_combined_and_scalar_queries(
    combined: bool,
) -> None:
    service, driver = _service(combined=combined)
    request = _frequency_request()

    result, artifact = service.configure_basic_v2(request, correlation_id="basic-write")

    assert result.basic.frequency_hz.value == 2_000.0
    assert driver.basic_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.basic_configure_v2"
    assert artifact["request"]["channel"] == 1
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "off",
    }
    assert [item["phase"] for item in artifact["phases"]] == [
        "preflight",
        "main",
        "postcondition",
    ]
    assert "fake-source-v2" not in repr(artifact)
    assert "SOURCE:STATE?" not in repr(artifact)


def test_basic_live_configure_v2_public_service_keeps_output_on() -> None:
    service, driver = _service(output_enabled=True, include_live=True)
    request = _frequency_request()

    result, artifact = service.configure_basic_live_v2(
        request,
        correlation_id="basic-live-write",
    )

    assert result.output_enabled is True
    assert result.basic.frequency_hz.value == 2_000.0
    assert driver.basic_requests == []
    assert driver.live_basic_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert driver.transport.counters.query_calls == 2
    assert artifact["operation"] == "source.basic_live_configure_v2"
    assert artifact["capability_decision"]["capability"] == (
        "source.basic_live_configure_v2"
    )
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "on",
    }


def test_v1_live_capable_basic_route_uses_off_transaction_when_output_is_off() -> None:
    service, driver = _service(include_live=True)

    status = service.set_frequency(channel=1, value_hz=2_000.0)

    assert status.output == "OFF"
    assert driver.basic_requests == [_frequency_request()]
    assert driver.live_basic_requests == []
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1


@pytest.mark.parametrize(
    ("method", "value", "expected_field"),
    (
        ("set_frequency", 2_000.0, "frequency_hz"),
        ("set_amplitude_vpp", 1.5, "amplitude_vpp"),
    ),
)
def test_v1_live_basic_routes_do_not_cycle_output(
    method: str,
    value: float,
    expected_field: str,
) -> None:
    service, driver = _service(output_enabled=True, include_live=True)

    status = getattr(service, method)(channel=1, **{
        "value_hz" if method == "set_frequency" else "value_vpp": value,
    })

    assert status.output == "ON"
    assert driver.basic_requests == []
    assert len(driver.live_basic_requests) == 1
    assert getattr(driver.live_basic_requests[0].patch, expected_field).value == value
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert driver.transport.counters.query_calls == 2


def test_frequency_response_style_live_sequence_never_cycles_output() -> None:
    service, driver = _service(output_enabled=True, include_live=True)

    for amplitude_vpp in (0.5, 1.0):
        service.set_amplitude_vpp(channel=1, value_vpp=amplitude_vpp)
        for frequency_hz in (100.0, 1_000.0):
            status = service.set_frequency(channel=1, value_hz=frequency_hz)
            assert status.output == "ON"

    assert len(driver.live_basic_requests) == 6
    assert driver.basic_requests == []
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 6
    assert driver.transport.counters.query_calls == 12


def test_basic_live_configure_v2_rejects_output_off_before_write() -> None:
    service, driver = _service(include_live=True)

    with pytest.raises(ConfigError, match="target output ON"):
        service.configure_basic_live_v2(_frequency_request())

    assert driver.live_basic_requests == []
    assert driver.output_requests == []
    assert driver.transport.counters.write_requests == 0


def test_basic_live_configure_v2_rejects_multiple_fields_before_write() -> None:
    service, driver = _service(output_enabled=True, include_live=True)
    request = SourceBasicConfigureRequest(
        channel=1,
        patch=SourceBasicPatch(
            frequency_hz=PatchValue(PatchAction.SET, 2_000.0),
            amplitude_vpp=PatchValue(PatchAction.SET, 1.5),
        ),
    )

    with pytest.raises(ConfigError, match="exactly one"):
        service.configure_basic_live_v2(request)

    assert driver.live_basic_requests == []
    assert driver.transport.counters.write_requests == 0


def test_basic_live_configure_v2_enforces_per_field_profile() -> None:
    service, driver = _service(
        output_enabled=True,
        include_live=True,
        live_frequency=False,
        live_amplitude_vpp=True,
    )

    with pytest.raises(ConfigError, match="frequency_hz is not declared"):
        service.configure_basic_live_v2(_frequency_request())

    assert driver.live_basic_requests == []
    assert driver.transport.counters.write_requests == 0


def test_basic_live_configure_v2_rejects_safety_limit_before_write() -> None:
    service, driver = _service(
        output_enabled=True,
        include_live=True,
        limits=SafetyLimitsConfig(max_source_vpp=2.0),
    )
    request = SourceBasicConfigureRequest(
        channel=1,
        patch=SourceBasicPatch(
            amplitude_vpp=PatchValue(PatchAction.SET, 2.5),
        ),
    )

    with pytest.raises(ConfigError, match="max_source_vpp"):
        service.configure_basic_live_v2(request)

    assert driver.live_basic_requests == []
    assert driver.transport.counters.write_requests == 0


def test_v1_frequency_route_maps_to_v2_for_a_dual_contract_driver() -> None:
    service, driver = _service()
    assert service.descriptor is not None
    service.descriptor = replace(
        service.descriptor,
        capabilities=(
            "source.snapshot_v2",
            "source.basic_configure_v2",
            "source.output_v2",
            "source.set_frequency",
        ),
    )

    status = service.set_frequency(channel=1, value_hz=2_000.0)

    assert status.channel == 1
    assert status.output == "OFF"
    assert status.frequency_hz == 2_000.0
    assert driver.basic_requests == [_frequency_request()]
    assert driver.transport.counters.write_completed == 1


@pytest.mark.parametrize(
    ("method", "value", "patch_field", "expected"),
    (
        ("set_frequency", 2_000.0, "frequency_hz", 2_000.0),
        ("set_function", "SIN", "waveform_kind", "sine"),
        ("set_amplitude_vpp", 1.5, "amplitude_vpp", 1.5),
        ("set_square_duty_cycle", 25.0, "square_duty_cycle_percent", 25.0),
    ),
)
def test_all_v1_basic_routes_use_the_v2_transaction_when_declared(
    method: str,
    value: object,
    patch_field: str,
    expected: object,
) -> None:
    service, driver = _service()

    if method == "set_frequency":
        service.set_frequency(channel=1, value_hz=value)
    elif method == "set_function":
        service.set_function(channel=1, function=value)
    elif method == "set_amplitude_vpp":
        service.set_amplitude_vpp(channel=1, value_vpp=value)
    else:
        service.set_square_duty_cycle(channel=1, duty_percent=value)

    assert len(driver.basic_requests) == 1
    patch_value = getattr(driver.basic_requests[0].patch, patch_field)
    assert patch_value.action is PatchAction.SET
    assert getattr(patch_value.value, "value", patch_value.value) == expected
    assert driver.transport.counters.write_completed == 1


@pytest.mark.parametrize(
    ("function", "expected_function"),
    (("noise", "NOIS"), ("dc", "DC")),
)
def test_v1_function_outside_the_v2_profile_keeps_its_legacy_route(
    function: str,
    expected_function: str,
) -> None:
    service, driver = _legacy_waveform_fallback_service()

    status = service.set_function(channel=1, function=function)

    assert status.function == expected_function
    assert driver.basic_requests == []
    assert driver.v1_function_requests == [(1, function, True)]
    assert driver.transport.counters.write_completed == 1


def test_v1_restore_route_uses_v2_basic_and_output_transactions() -> None:
    service, driver = _service()

    status = service.restore_restorable_state(
        RestorableSourceState(
            channel=1,
            output="OFF",
            function="SIN",
            frequency_hz=1_000.0,
            amplitude_vpp=1.0,
            amplitude_unit="VPP",
        )
    )

    assert status.output == "OFF"
    assert driver.basic_requests == [
        SourceBasicConfigureRequest(
            channel=1,
            patch=SourceBasicPatch(
                waveform_kind=PatchValue(PatchAction.SET, SourceWaveformKind.SINE),
            ),
        ),
        SourceBasicConfigureRequest(
            channel=1,
            patch=SourceBasicPatch(
                amplitude_vpp=PatchValue(PatchAction.SET, 1.0),
            ),
        ),
        SourceBasicConfigureRequest(
            channel=1,
            patch=SourceBasicPatch(
                frequency_hz=PatchValue(PatchAction.SET, 1_000.0),
            ),
        ),
    ]
    assert driver.output_requests == []
    assert driver.transport.counters.write_requests == 3


def test_v1_restore_v2_splits_square_duty_after_waveform_restore() -> None:
    service, driver = _service(
        waveform_kinds=(SourceWaveformKind.SINE, SourceWaveformKind.SQUARE),
        square_duty_readable=True,
    )

    service.restore_restorable_state(
        RestorableSourceState(
            channel=1,
            output="OFF",
            function="SQU",
            frequency_hz=1_000.0,
            amplitude_vpp=1.0,
            amplitude_unit="VPP",
            square_duty_cycle_percent=25.0,
        )
    )

    assert [
        next(
            name
            for name, value in (
                ("waveform_kind", request.patch.waveform_kind),
                ("amplitude_vpp", request.patch.amplitude_vpp),
                ("frequency_hz", request.patch.frequency_hz),
                ("square_duty_cycle_percent", request.patch.square_duty_cycle_percent),
            )
            if value.action is PatchAction.SET
        )
        for request in driver.basic_requests
    ] == [
        "waveform_kind",
        "amplitude_vpp",
        "frequency_hz",
        "square_duty_cycle_percent",
    ]


def test_restorable_snapshot_uses_v2_when_the_full_restore_route_is_declared() -> None:
    service, driver = _service()

    state = service.snapshot_restorable_state(channel=1)

    assert state == RestorableSourceState(
        channel=1,
        output="OFF",
        function="SIN",
        frequency_hz=1_000.0,
        amplitude_vpp=1.0,
        amplitude_unit="VPP",
    )
    assert driver.transport.counters.query_calls > 0
    assert driver.transport.counters.write_requests == 0


def test_v2_restore_rejects_unmappable_waveform_before_turning_output_off() -> None:
    service, driver = _service(output_enabled=True)

    with pytest.raises(ConfigError, match="cannot map this waveform"):
        service.restore_restorable_state(
            RestorableSourceState(
                channel=1,
                output="ON",
                function="USER",
                frequency_hz=1_000.0,
                amplitude_vpp=1.0,
                amplitude_unit="VPP",
            )
        )

    assert driver.basic_requests == []
    assert driver.output_requests == []
    assert driver.transport.counters.write_requests == 0


def test_v1_restore_route_restores_original_on_state_through_v2_output() -> None:
    service, driver = _service(output_enabled=True)

    status = service.restore_restorable_state(
        RestorableSourceState(
            channel=1,
            output="ON",
            function="SIN",
            frequency_hz=1_000.0,
            amplitude_vpp=1.0,
            amplitude_unit="VPP",
        )
    )

    assert status.output == "ON"
    assert driver.output_requests == [
        SourceOutputRequest(channel=1, enabled=False),
        SourceOutputRequest(channel=1, enabled=True),
    ]
    assert len(driver.basic_requests) == 3
    assert driver.transport.counters.write_requests == 5


def test_v1_restore_route_rejects_partial_v2_restore_before_io() -> None:
    service, driver = _service(include_output=False)

    with pytest.raises(ConfigError, match="restore_restorable_state cannot run"):
        service.restore_restorable_state(
            RestorableSourceState(
                channel=1,
                output="OFF",
                function="SIN",
                frequency_hz=1_000.0,
                amplitude_vpp=1.0,
                amplitude_unit="VPP",
            )
        )

    assert driver.transport.counters.write_requests == 0


@pytest.mark.parametrize(
    "operation",
    ("upload", "trigger_burst", "trigger_sweep"),
)
def test_overlapping_v1_routes_reject_before_io_for_a_dual_contract_driver(operation: str) -> None:
    service, driver = _service()

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        if operation == "upload":
            service.upload_arbitrary_waveform(
                channel=1,
                file_path="unused.npy",
                playback_frequency_hz=1_000.0,
                amplitude_vpp=1.0,
            )
        elif operation == "trigger_burst":
            service.trigger_burst(channel=1)
        else:
            service.trigger_sweep(channel=1)

    assert driver.transport.counters.write_requests == 0


def test_dual_contract_driver_classifies_every_v1_write_route() -> None:
    service, driver = _dual_contract_service()

    service.set_frequency(channel=1, value_hz=2_000.0)
    service.set_function(channel=1, function="SIN")
    service.set_amplitude_vpp(channel=1, value_vpp=1.5)
    service.set_square_duty_cycle(channel=1, duty_percent=25.0)

    mapped_routes = {
        SourceV1WriteRouteId.SET_FREQUENCY,
        SourceV1WriteRouteId.SET_FUNCTION,
        SourceV1WriteRouteId.SET_AMPLITUDE_VPP,
        SourceV1WriteRouteId.SET_SQUARE_DUTY_CYCLE,
        SourceV1WriteRouteId.SET_OUTPUT,
        SourceV1WriteRouteId.RESTORE,
    }
    assert len(driver.basic_requests) == 4

    service.configure_coupling(
        SourceCouplingConfiguration(1, True, 1_000.0, True, 90.0, False, 2.0)
    )
    service.configure_harmonics(
        1,
        SourceHarmonicConfiguration(order=8, preset="ODD"),
        check_errors=False,
    )
    service.configure_am_modulation(
        SourceAmModulationConfiguration(True, 80.0, 25.0, "SINE"),
        channel=1,
    )
    service.configure_fm_modulation(
        SourceFmModulationConfiguration(True, 250.0, 25.0, "SINE"),
        channel=1,
    )
    service.configure_pm_modulation(
        SourcePmModulationConfiguration(True, 90.0, 25.0, "SINE"),
        channel=1,
    )
    service.configure_pwm_modulation(
        SourcePwmModulationConfiguration(True, "WIDTH", 0.001, 25.0, "SINE"),
        channel=1,
    )
    service.configure_pulse(
        SourcePulseConfiguration(
            hold="WIDTH",
            width_s=1.0e-6,
            delay_s=0.0,
            leading_transition_s=8.0e-9,
            trailing_transition_s=8.0e-9,
        ),
        channel=1,
    )
    service.configure_burst(
        SourceBurstConfiguration(
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
        ),
        channel=1,
    )
    service.configure_sweep(
        SourceSweepConfiguration(
            enabled=True,
            start_hz=100.0,
            stop_hz=1_000.0,
            spacing="LINEAR",
            steps=101,
            sweep_time_s=1.0,
            start_hold_s=0.0,
            stop_hold_s=0.0,
            return_time_s=0.0,
            trigger_source="MANUAL",
            trigger_slope="POSITIVE",
            trigger_out="OFF",
            marker_enabled=False,
            marker_frequency_hz=550.0,
        ),
        channel=1,
    )
    service.set_output(channel=1, enabled=True)
    assert driver.output_requests[-1] == SourceOutputRequest(channel=1, enabled=True)
    assert driver.v1_output_calls == 0

    disjoint_routes = {
        SourceV1WriteRouteId.CONFIGURE_COUPLING,
        SourceV1WriteRouteId.CONFIGURE_HARMONICS,
        SourceV1WriteRouteId.CONFIGURE_AM,
        SourceV1WriteRouteId.CONFIGURE_FM,
        SourceV1WriteRouteId.CONFIGURE_PM,
        SourceV1WriteRouteId.CONFIGURE_PWM,
        SourceV1WriteRouteId.CONFIGURE_PULSE,
        SourceV1WriteRouteId.CONFIGURE_BURST,
        SourceV1WriteRouteId.CONFIGURE_SWEEP,
    }
    assert driver.legacy_calls == [
        "configure_coupling",
        "configure_harmonics",
        "configure_am_modulation",
        "configure_fm_modulation",
        "configure_pm_modulation",
        "configure_pwm_modulation",
        "configure_pulse",
        "configure_burst",
        "configure_sweep",
    ]

    service.restore_restorable_state(
        RestorableSourceState(
            channel=1,
            output="OFF",
            function="SIN",
            frequency_hz=1_000.0,
            amplitude_vpp=1.0,
            amplitude_unit="VPP",
        )
    )
    writes_before_rejections = driver.transport.counters.write_requests
    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.trigger_burst(channel=1)
    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.trigger_sweep(channel=1)
    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.upload_arbitrary_waveform(
            channel=1,
            file_path="unused.npy",
            playback_frequency_hz=1_000.0,
            amplitude_vpp=1.0,
        )
    rejected_routes = {
        SourceV1WriteRouteId.TRIGGER_BURST,
        SourceV1WriteRouteId.TRIGGER_SWEEP,
        SourceV1WriteRouteId.UPLOAD_ARBITRARY,
    }
    assert driver.transport.counters.write_requests == writes_before_rejections
    assert mapped_routes | disjoint_routes | rejected_routes == set(SourceV1WriteRouteId)


def test_basic_configure_v2_rejects_target_output_on_before_write() -> None:
    service, driver = _service(output_enabled=True)

    with pytest.raises(ConfigError, match="target output OFF"):
        service._configure_basic_v2_transaction(_frequency_request())

    assert driver.basic_requests == []
    assert driver.output_requests == []
    assert driver.transport.counters.write_requests == 0
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_basic_configure_v2_rejects_configured_limits_before_write() -> None:
    service, driver = _service(limits=SafetyLimitsConfig(max_source_vpp=2.0))
    request = SourceBasicConfigureRequest(
        channel=1,
        patch=SourceBasicPatch(
            amplitude_vpp=PatchValue(PatchAction.SET, 2.5),
        ),
    )

    with pytest.raises(ConfigError, match="max_source_vpp"):
        service._configure_basic_v2_transaction(request)

    assert driver.basic_requests == []
    assert driver.transport.counters.write_requests == 0


def test_basic_configure_v2_rejects_configured_absolute_port_limits_before_write() -> None:
    service, driver = _service(
        limits=SafetyLimitsConfig(
            min_source_port_voltage_v=-1.0,
            max_source_port_voltage_v=1.0,
        )
    )
    request = SourceBasicConfigureRequest(
        channel=1,
        patch=SourceBasicPatch(offset_v=PatchValue(PatchAction.SET, 1.0)),
    )

    with pytest.raises(ConfigError, match="port voltage"):
        service._configure_basic_v2_transaction(request)

    assert driver.basic_requests == []
    assert driver.transport.counters.write_requests == 0


def test_basic_configure_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _service(postcondition_frequency_hz=2_001.0)

    with pytest.raises(ConfigError, match="frequency_hz readback") as raised:
        service._configure_basic_v2_transaction(_frequency_request())

    artifact = raised.value.source_operation_artifact
    assert driver.basic_requests == [_frequency_request()]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert driver.transport.counters.write_completed == 2
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }
    assert artifact["safe_state_verified"] is True
    assert artifact["final_state"]["session_health"] == "uncertain"
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN


def test_basic_live_configure_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _service(
        output_enabled=True,
        include_live=True,
        postcondition_frequency_hz=2_001.0,
    )

    with pytest.raises(ConfigError, match="frequency_hz readback") as raised:
        service.configure_basic_live_v2(_frequency_request())

    artifact = raised.value.source_operation_artifact
    assert driver.basic_requests == []
    assert driver.live_basic_requests == [_frequency_request()]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert driver.transport.counters.write_completed == 2
    assert artifact["operation"] == "source.basic_live_configure_v2"
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }
    assert artifact["safe_state_verified"] is True
    assert artifact["final_state"]["session_health"] == "uncertain"
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN


def test_basic_live_configure_v2_failure_is_not_retried() -> None:
    service, driver = _service(
        output_enabled=True,
        include_live=True,
        raise_after_write=True,
    )

    with pytest.raises(ConfigError, match="failed after write"):
        service.configure_basic_live_v2(_frequency_request())

    assert driver.live_basic_requests == [_frequency_request()]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert driver.transport.counters.write_requests == 2


def test_basic_configure_v2_never_falls_back_to_v1_output_for_recovery() -> None:
    service, driver = _service(include_output=False, raise_after_write=True)

    with pytest.raises(ConfigError, match="failed after write") as raised:
        service._configure_basic_v2_transaction(_frequency_request())

    artifact = raised.value.source_operation_artifact
    assert driver.basic_requests == [_frequency_request()]
    assert driver.output_requests == []
    assert driver.v1_output_calls == 0
    assert artifact["recovery"] == {
        "status": "not_attempted",
        "reason": "output_capability_unavailable",
    }
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.POISONED
