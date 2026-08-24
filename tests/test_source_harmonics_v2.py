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
from wavebench.instruments.source_extension_capabilities import validate_source_descriptor
from wavebench.instruments.source_extensions import (
    SOURCE_CONTRACT_VERSION,
    Availability,
    ComponentAmplitudeKind,
    HarmonicCompleteness,
    HarmonicFacet,
    Observed,
    SourceConstraintApplicability,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFieldId,
    SourceHarmonicCapabilityProfile,
    SourceHarmonicConfigureRequest,
    SourceHarmonicConfigureResult,
    SourceHarmonicDisableRequest,
    SourceHarmonicDisableResult,
    SourceHarmonicPreset,
    OutputFacet,
    SourceOutputPolarity,
    SourceOutputRequest,
    SourceOutputResult,
    SourceProtocolQueryRecord,
    SourceQueryEffect,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceReasonCode,
    SourceRuntimeIdentity,
    SourceTypedObservation,
    SupportState,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState

from tests.source_v2_fixtures import basic_facet, source_descriptor, source_extensions


class _TextTransport:
    resource = "fake-source-harmonics-v2"

    def record_event(self, direction: str, text: str) -> None:
        del direction, text

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        del command, replay
        return "ok"

    def write(self, command: str) -> None:
        del command

    def close(self) -> None:
        pass


def _missing() -> Observed[object]:
    return Observed.missing(Availability.NOT_QUERIED, SourceReasonCode.NOT_REQUESTED)


def _harmonics(
    *,
    enabled: bool,
    order: int | None = None,
    preset: SourceHarmonicPreset | None = None,
) -> HarmonicFacet:
    return HarmonicFacet(
        enabled=Observed.value_of(enabled),
        completeness=_missing(),
        maximum_supported_order=Observed.value_of(16),
        components=_missing(),
        configured_order=(Observed.value_of(order) if order is not None else _missing()),
        preset=(Observed.value_of(preset) if preset is not None else _missing()),
    )


class _HarmonicWriteDriver:
    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        output_enabled: bool = False,
        postcondition_mismatch: bool = False,
        raise_after_write: bool = False,
        harmonic_enabled: bool = False,
        disable_postcondition_mismatch: bool = False,
        raise_after_disable: bool = False,
    ) -> None:
        self.transport = GuardedAuditedTransport(
            _TextTransport(),
            session_state=session_state,
        )
        self.output_enabled = output_enabled
        self.postcondition_mismatch = postcondition_mismatch
        self.raise_after_write = raise_after_write
        self.disable_postcondition_mismatch = disable_postcondition_mismatch
        self.raise_after_disable = raise_after_disable
        self.harmonics = _harmonics(enabled=harmonic_enabled)
        self.harmonic_requests: list[SourceHarmonicConfigureRequest] = []
        self.harmonic_disable_requests: list[SourceHarmonicDisableRequest] = []
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_harmonic_calls = 0

    def close(self) -> None:
        self.transport.close()

    def execute_source_query_plan_v2(self, plan) -> SourceQueryExecutionRecord:
        records = []
        for index, item in enumerate(plan.items):
            if index == 0:
                self.transport.query("SOURCE:STATE?")
            observations = []
            for field in item.fields:
                if field.field is SourceFieldId.IDENTITY:
                    value = SourceRuntimeIdentity(
                        manufacturer="Example",
                        model="EX1",
                        firmware_id="1.0",
                    )
                elif field.field is SourceFieldId.OUTPUT:
                    value = self._output()
                elif field.field is SourceFieldId.BASIC:
                    value = basic_facet()
                elif field.field is SourceFieldId.HARMONICS:
                    value = self._readback_harmonics()
                else:  # pragma: no cover - this descriptor only declares these fields.
                    raise AssertionError(field)
                observations.append(SourceTypedObservation(field, value))
            records.append(
                SourceProtocolQueryRecord(
                    item_id=item.item_id,
                    effect=item.effect,
                    outcome=SourceQueryItemOutcome.OBSERVED,
                    query_count=(1 if index == 0 else 0),
                    observations=tuple(observations),
                )
            )
        return SourceQueryExecutionRecord(
            contract_version=SOURCE_CONTRACT_VERSION,
            plan_id=plan.plan_id,
            items=tuple(records),
            query_count=1,
            device_revision_token_before="revision-1",
            device_revision_token_after="revision-1",
        )

    def configure_source_harmonics_v2(
        self,
        request: SourceHarmonicConfigureRequest,
    ) -> SourceHarmonicConfigureResult:
        self.transport.write("SOURCE:HARMONICS:CONFIGURE")
        self.harmonic_requests.append(request)
        self.harmonics = _harmonics(
            enabled=True,
            order=request.order,
            preset=request.preset,
        )
        if self.raise_after_write:
            raise ConfigError("fake harmonic configure failed after write")
        return SourceHarmonicConfigureResult(
            channel=request.channel,
            harmonics=self.harmonics,
            output_enabled=False,
        )

    def disable_source_harmonics_v2(
        self,
        request: SourceHarmonicDisableRequest,
    ) -> SourceHarmonicDisableResult:
        self.transport.write("SOURCE:HARMONICS:DISABLE")
        self.harmonic_disable_requests.append(request)
        self.harmonics = _harmonics(enabled=False)
        if self.raise_after_disable:
            raise ConfigError("fake harmonic disable failed after write")
        return SourceHarmonicDisableResult(
            channel=request.channel,
            harmonics=self.harmonics,
            output_enabled=False,
        )

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT")
        self.output_requests.append(request)
        self.output_enabled = request.enabled
        if request.enabled:
            raise AssertionError("the harmonic fixture only uses recovery OFF")
        return SourceOutputResult(channel=request.channel, enabled=False)

    def configure_harmonics(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.v1_harmonic_calls += 1
        raise AssertionError("dual-contract V1 harmonic route must not reach the V1 driver")

    def _output(self):
        return OutputFacet(
            enabled=Observed.value_of(self.output_enabled),
            display_load=_missing(),
            polarity=Observed.value_of(SourceOutputPolarity.NORMAL),
        )

    def _readback_harmonics(self) -> HarmonicFacet:
        if self.disable_postcondition_mismatch and self.harmonic_disable_requests:
            return _harmonics(
                enabled=True,
                order=8,
                preset=SourceHarmonicPreset.ODD,
            )
        if not self.postcondition_mismatch or not self.harmonic_requests:
            return self.harmonics
        request = self.harmonic_requests[-1]
        return _harmonics(
            enabled=True,
            order=request.order + 1,
            preset=request.preset,
        )


def _extensions(
    *,
    presets: tuple[SourceHarmonicPreset, ...] | None = None,
    directions: tuple[SourceFeatureDirection, ...] = (
        SourceFeatureDirection.CONFIGURE,
        SourceFeatureDirection.READ,
    ),
):
    base = source_extensions()
    basic, output = base.features
    harmonic = SourceFeatureCapability(
        feature=SourceFeature.HARMONICS,
        support=SupportState.SUPPORTED,
        directions=directions,
        scope=SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=SourceConstraintApplicability(),
        profile=SourceHarmonicCapabilityProfile(
            minimum_order=2,
            maximum_order=16,
            amplitude_kinds=(ComponentAmplitudeKind.ABSOLUTE_VPP,),
            completeness_modes=(HarmonicCompleteness.ACTIVE_ONLY,),
            presets=presets
            or (
                SourceHarmonicPreset.ALL,
                SourceHarmonicPreset.EVEN,
                SourceHarmonicPreset.ODD,
            ),
            configured_order_readable=True,
            preset_readable=True,
        ),
    )
    harmonic_query = SourceFacetQueryContract(
        feature=SourceFeature.HARMONICS,
        scope=SourceFacetScope.CHANNEL,
        fields=(SourceFieldId.HARMONICS,),
        activation_any=(),
        effect=SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    return replace(
        base,
        features=(
            basic,
            harmonic,
            replace(
                output,
                directions=(
                    SourceFeatureDirection.DISABLE,
                    SourceFeatureDirection.ENABLE,
                    SourceFeatureDirection.READ,
                ),
            ),
        ),
        query_contract=replace(
            base.query_contract,
            facets=(
                base.query_contract.facets[0],
                base.query_contract.facets[1],
                harmonic_query,
                base.query_contract.facets[2],
            ),
            max_queries=7,
        ),
    )


def _config() -> WaveBenchConfig:
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
            False,
            True,
            0,
        ),
        safety_limits=SafetyLimitsConfig(),
    )


def _service(
    *,
    output_enabled: bool = False,
    postcondition_mismatch: bool = False,
    raise_after_write: bool = False,
    presets: tuple[SourceHarmonicPreset, ...] | None = None,
    dual_contract: bool = False,
    harmonic_capability: str = "source.harmonics_configure_v2",
    harmonic_directions: tuple[SourceFeatureDirection, ...] = (
        SourceFeatureDirection.CONFIGURE,
        SourceFeatureDirection.READ,
    ),
    harmonic_enabled: bool = False,
    disable_postcondition_mismatch: bool = False,
    raise_after_disable: bool = False,
) -> tuple[SourceService, _HarmonicWriteDriver]:
    session_state = InstrumentSessionState(epoch_id="source-harmonics-v2")
    driver = _HarmonicWriteDriver(
        session_state=session_state,
        output_enabled=output_enabled,
        postcondition_mismatch=postcondition_mismatch,
        raise_after_write=raise_after_write,
        harmonic_enabled=harmonic_enabled,
        disable_postcondition_mismatch=disable_postcondition_mismatch,
        raise_after_disable=raise_after_disable,
    )
    capabilities = [
        "source.snapshot_v2",
        harmonic_capability,
        "source.output_v2",
    ]
    if dual_contract:
        capabilities.append("source.harmonic_configure")
    descriptor = replace(
        source_descriptor(
            driver=driver,
            extensions=_extensions(presets=presets, directions=harmonic_directions),
        ),
        capabilities=tuple(capabilities),
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


def _request(
    *,
    order: int = 8,
    preset: SourceHarmonicPreset = SourceHarmonicPreset.ODD,
) -> SourceHarmonicConfigureRequest:
    return SourceHarmonicConfigureRequest(channel=1, order=order, preset=preset)


def _disable_service(
    *,
    output_enabled: bool = False,
    harmonic_enabled: bool = True,
    disable_postcondition_mismatch: bool = False,
    raise_after_disable: bool = False,
    dual_contract: bool = False,
) -> tuple[SourceService, _HarmonicWriteDriver]:
    return _service(
        output_enabled=output_enabled,
        harmonic_enabled=harmonic_enabled,
        disable_postcondition_mismatch=disable_postcondition_mismatch,
        raise_after_disable=raise_after_disable,
        dual_contract=dual_contract,
        harmonic_capability="source.harmonics_disable_v2",
        harmonic_directions=(
            SourceFeatureDirection.DISABLE,
            SourceFeatureDirection.READ,
        ),
    )


def test_harmonic_configure_v2_writes_once_and_keeps_output_off() -> None:
    service, driver = _service()
    request = _request()

    result, artifact = service.configure_harmonics_v2(request, correlation_id="harmonic-write")

    assert result.harmonics.configured_order.value == 8
    assert result.harmonics.preset.value is SourceHarmonicPreset.ODD
    assert driver.harmonic_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.harmonics_configure_v2"
    assert artifact["request"]["order"] == 8
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "off",
    }
    assert [item["phase"] for item in artifact["phases"]] == [
        "preflight",
        "main",
        "postcondition",
    ]
    assert "fake-source-harmonics-v2" not in repr(artifact)


def test_harmonic_configure_v2_requires_output_off_before_driver_write() -> None:
    service, driver = _service(output_enabled=True)

    with pytest.raises(ConfigError, match="target output OFF"):
        service.configure_harmonics_v2(_request())

    assert driver.harmonic_requests == []
    assert driver.transport.counters.write_requests == 0


def test_harmonic_configure_v2_uses_runtime_preset_profile_before_driver_write() -> None:
    service, driver = _service(presets=(SourceHarmonicPreset.ALL,))

    with pytest.raises(ConfigError, match="preset is not supported"):
        service.configure_harmonics_v2(_request())

    assert driver.harmonic_requests == []
    assert driver.transport.counters.write_requests == 0


def test_harmonic_configure_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _service(postcondition_mismatch=True)

    with pytest.raises(ConfigError, match="configured order readback does not match") as raised:
        service.configure_harmonics_v2(_request())

    artifact = raised.value.source_operation_artifact
    assert driver.harmonic_requests == [_request()]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }


def test_harmonic_disable_v2_writes_once_and_keeps_output_off() -> None:
    service, driver = _disable_service()
    request = SourceHarmonicDisableRequest(channel=1)

    result, artifact = service.disable_harmonics_v2(request, correlation_id="harmonic-disable")

    assert result.harmonics.enabled.value is False
    assert driver.harmonic_disable_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.harmonics_disable_v2"
    assert artifact["request"] == {
        "type": "SourceHarmonicDisableRequest",
        "channel": 1,
    }
    assert artifact["mutation"]["status"] == "written"
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "off",
    }
    assert [item["phase"] for item in artifact["phases"]] == [
        "preflight",
        "main",
        "postcondition",
    ]


def test_harmonic_disable_v2_requires_output_off_before_driver_write() -> None:
    service, driver = _disable_service(output_enabled=True)

    with pytest.raises(ConfigError, match="target output OFF"):
        service.disable_harmonics_v2(SourceHarmonicDisableRequest(channel=1))

    assert driver.harmonic_disable_requests == []
    assert driver.transport.counters.write_requests == 0


def test_harmonic_disable_v2_is_a_zero_write_noop_when_already_disabled() -> None:
    service, driver = _disable_service(harmonic_enabled=False)

    result, artifact = service.disable_harmonics_v2(SourceHarmonicDisableRequest(channel=1))

    assert result.harmonics.enabled.value is False
    assert driver.harmonic_disable_requests == []
    assert driver.transport.counters.write_requests == 0
    assert artifact["mutation"]["status"] == "already_at_target"
    assert [item["phase"] for item in artifact["phases"]] == ["preflight"]


def test_harmonic_disable_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _disable_service(disable_postcondition_mismatch=True)

    with pytest.raises(ConfigError, match="postcondition reports harmonics enabled") as raised:
        service.disable_harmonics_v2(SourceHarmonicDisableRequest(channel=1))

    artifact = raised.value.source_operation_artifact
    assert driver.harmonic_disable_requests == [SourceHarmonicDisableRequest(channel=1)]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }


def test_harmonic_disable_v2_driver_failure_after_write_runs_one_off_recovery() -> None:
    service, driver = _disable_service(raise_after_disable=True)

    with pytest.raises(ConfigError, match="failed after write") as raised:
        service.disable_harmonics_v2(SourceHarmonicDisableRequest(channel=1))

    artifact = raised.value.source_operation_artifact
    assert driver.harmonic_disable_requests == [SourceHarmonicDisableRequest(channel=1)]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }


def test_v1_harmonic_route_rejects_before_io_for_a_harmonic_disable_v2_driver() -> None:
    from wavebench.instruments.models import SourceHarmonicConfiguration

    service, driver = _disable_service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.configure_harmonics(
            1,
            SourceHarmonicConfiguration(order=8, preset="ODD"),
            check_errors=False,
        )

    assert driver.v1_harmonic_calls == 0
    assert driver.transport.counters.write_requests == 0


def test_v1_harmonic_route_rejects_before_io_for_a_dual_contract_driver() -> None:
    from wavebench.instruments.models import SourceHarmonicConfiguration

    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.configure_harmonics(
            1,
            SourceHarmonicConfiguration(order=8, preset="ODD"),
            check_errors=False,
        )

    assert driver.v1_harmonic_calls == 0
    assert driver.transport.counters.write_requests == 0


def test_v1_restore_rejects_before_io_for_a_harmonic_v2_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.restore_restorable_state(object())  # type: ignore[arg-type]

    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0
