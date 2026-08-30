from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
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
    ArbitraryFacet,
    Availability,
    Observed,
    OutputFacet,
    SourceArbitraryCapabilityProfile,
    SourceArbitraryPlaybackMode,
    SourceArbitrarySelectRequest,
    SourceArbitrarySelectResult,
    SourceArbitraryStorageRequest,
    SourceArbitraryStorageResult,
    SourceArbitraryStorageSlot,
    SourceArbitraryVolatileReplaceRequest,
    SourceArbitraryVolatileReplaceResult,
    SourceConstraintApplicability,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFieldId,
    SourceOutputPolarity,
    SourceOutputRequest,
    SourceOutputResult,
    SourceProtocolQueryRecord,
    SourceQueryEffect,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceReasonCode,
    SourceRuntimeIdentity,
    SourceStorageWriteMode,
    SourceTypedObservation,
    SourceWaveformKind,
    SupportState,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState

from tests.source_v2_fixtures import basic_facet, source_descriptor, source_extensions


class _TextTransport:
    resource = "fake-source-arbitrary-v2"

    def record_event(self, direction: str, text: str) -> None:
        del direction, text

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        del command, replay
        return "ok"

    def write(self, command: str) -> None:
        del command

    def write_bytes(self, command: bytes) -> None:
        del command

    def close(self) -> None:
        pass


def _missing() -> Observed[object]:
    return Observed.missing(Availability.NOT_QUERIED, SourceReasonCode.NOT_REQUESTED)


def _digest(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def _arbitrary(
    *,
    slot_id: str | None = None,
    playback_mode: SourceArbitraryPlaybackMode | None = None,
    playback_frequency_hz: float | None = None,
    sample_rate_hz: float | None = None,
    point_count: int | None = None,
    storage_digest: str | None = None,
) -> ArbitraryFacet:
    return ArbitraryFacet(
        selected_waveform_id=(
            _missing() if slot_id is None else Observed.value_of(slot_id)
        ),
        playback_mode=(
            _missing() if playback_mode is None else Observed.value_of(playback_mode)
        ),
        playback_frequency_hz=(
            _missing()
            if playback_frequency_hz is None
            else Observed.value_of(playback_frequency_hz)
        ),
        sample_rate_hz=(
            _missing() if sample_rate_hz is None else Observed.value_of(sample_rate_hz)
        ),
        point_count=(
            _missing() if point_count is None else Observed.value_of(point_count)
        ),
        storage_digest=(
            _missing() if storage_digest is None else Observed.value_of(storage_digest)
        ),
    )


class _ArbitraryWriteDriver:
    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        output_enabled: bool = False,
        postcondition_mismatch: bool = False,
        volatile_write_error: bool = False,
    ) -> None:
        self.transport = GuardedAuditedTransport(
            _TextTransport(),
            session_state=session_state,
        )
        self.output_enabled = output_enabled
        self.postcondition_mismatch = postcondition_mismatch
        self.volatile_write_error = volatile_write_error
        self.basic = basic_facet()
        self.arbitrary = _arbitrary()
        self.slots: dict[str, bytes] = {}
        self.storage_requests: list[tuple[SourceArbitraryStorageRequest, bytes]] = []
        self.select_requests: list[SourceArbitrarySelectRequest] = []
        self.volatile_requests: list[tuple[SourceArbitraryVolatileReplaceRequest, bytes]] = []
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_upload_calls = 0

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
                elif field.field is SourceFieldId.BASIC:
                    value = self.basic
                elif field.field is SourceFieldId.OUTPUT:
                    value = self._output()
                elif field.field is SourceFieldId.ARBITRARY_SELECTION:
                    value = self._readback_arbitrary()
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

    def read_source_arbitrary_storage_v2(
        self,
        channel: int,
        slot_id: str,
    ) -> SourceArbitraryStorageSlot:
        self.transport.query("SOURCE:ARB:STORAGE?")
        payload = self.slots.get(slot_id)
        if payload is None:
            return SourceArbitraryStorageSlot(channel, slot_id, False)
        return SourceArbitraryStorageSlot(
            channel,
            slot_id,
            True,
            _digest(payload),
            len(payload),
        )

    def mutate_source_arbitrary_storage_v2(
        self,
        request: SourceArbitraryStorageRequest,
        payload: bytes,
    ) -> SourceArbitraryStorageResult:
        self.transport.write_bytes(payload)
        previous = self.slots.get(request.slot_id)
        if request.write_mode is SourceStorageWriteMode.CREATE_ONLY and previous is not None:
            raise AssertionError("driver create-only mutation must be preflight-rejected")
        if (
            request.write_mode is SourceStorageWriteMode.REPLACE_IF_DIGEST_MATCHES
            and (previous is None or _digest(previous) != request.expected_previous_sha256)
        ):
            raise AssertionError("driver CAS mutation must be preflight-rejected")
        self.storage_requests.append((request, payload))
        self.slots[request.slot_id] = payload
        return SourceArbitraryStorageResult(
            request.channel,
            request.slot_id,
            request.payload_sha256,
            request.payload_size_bytes,
            True,
            False,
            True,
        )

    def select_source_arbitrary_v2(
        self,
        request: SourceArbitrarySelectRequest,
    ) -> SourceArbitrarySelectResult:
        self.transport.write("SOURCE:ARB:SELECT")
        payload = self.slots.get(request.slot_id)
        if payload is None:
            raise ConfigError("fake source arbitrary slot is absent")
        self.select_requests.append(request)
        self.basic = replace(
            self.basic,
            waveform_kind=Observed.value_of(SourceWaveformKind.ARBITRARY),
            waveform_id=Observed.value_of(request.slot_id),
        )
        self.arbitrary = _arbitrary(
            slot_id=request.slot_id,
            playback_mode=request.playback_mode,
            playback_frequency_hz=(
                request.playback_frequency_hz
                if request.playback_mode is SourceArbitraryPlaybackMode.DDS
                else 1.0
            ),
            sample_rate_hz=(
                request.sample_rate_hz
                if request.playback_mode is SourceArbitraryPlaybackMode.TRUE_ARB
                else float(len(payload))
            ),
            point_count=len(payload),
            storage_digest=_digest(payload),
        )
        return SourceArbitrarySelectResult(
            request.channel,
            self.basic,
            self.arbitrary,
            False,
        )

    def replace_source_arbitrary_volatile_v2(
        self,
        request: SourceArbitraryVolatileReplaceRequest,
        payload: bytes,
    ) -> SourceArbitraryVolatileReplaceResult:
        self.transport.write_bytes(payload)
        self.volatile_requests.append((request, payload))
        if self.volatile_write_error:
            raise ConfigError("fake volatile binary write result is unknown")
        self.basic = replace(
            self.basic,
            waveform_kind=Observed.value_of(SourceWaveformKind.ARBITRARY),
            waveform_id=Observed.value_of("volatile"),
        )
        self.arbitrary = _arbitrary(slot_id="volatile")
        return SourceArbitraryVolatileReplaceResult(
            request.channel,
            request.payload_sha256,
            request.payload_size_bytes,
            request.point_count,
            "volatile",
            True,
            False,
            False,
        )

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT")
        self.output_requests.append(request)
        self.output_enabled = request.enabled
        if request.enabled:
            raise AssertionError("the arbitrary fixture only uses recovery OFF")
        return SourceOutputResult(channel=request.channel, enabled=False)

    def upload_dg4000_dac14_block(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.v1_upload_calls += 1
        raise AssertionError("dual-contract V1 ARB upload must not reach the driver")

    def _output(self) -> OutputFacet:
        return OutputFacet(
            enabled=Observed.value_of(self.output_enabled),
            display_load=_missing(),
            polarity=Observed.value_of(SourceOutputPolarity.NORMAL),
        )

    def _readback_arbitrary(self) -> ArbitraryFacet:
        if not self.postcondition_mismatch or not (
            self.select_requests or self.volatile_requests
        ):
            return self.arbitrary
        return replace(
            self.arbitrary,
            selected_waveform_id=Observed.value_of("wrong_slot"),
        )


def _extensions(
    *,
    playback_modes: tuple[SourceArbitraryPlaybackMode, ...] = (
        SourceArbitraryPlaybackMode.DDS,
        SourceArbitraryPlaybackMode.TRUE_ARB,
    ),
):
    base = source_extensions()
    basic, output = base.features
    arbitrary = SourceFeatureCapability(
        feature=SourceFeature.ARBITRARY,
        support=SupportState.SUPPORTED,
        directions=(SourceFeatureDirection.CONFIGURE, SourceFeatureDirection.READ),
        scope=SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=SourceConstraintApplicability(),
        profile=SourceArbitraryCapabilityProfile(
            playback_modes=playback_modes,
            selection_readable=True,
            storage_metadata_readable=True,
            sample_rate_readable=True,
            storage_slot_metadata_readable=True,
            storage_write_modes=(
                SourceStorageWriteMode.CREATE_ONLY,
                SourceStorageWriteMode.REPLACE_IF_DIGEST_MATCHES,
            ),
            storage_max_payload_bytes=4096,
            volatile_replace_min_points=2,
            volatile_replace_max_points=16_384,
            volatile_replace_max_payload_bytes=32_768,
        ),
    )
    arbitrary_query = SourceFacetQueryContract(
        feature=SourceFeature.ARBITRARY,
        scope=SourceFacetScope.CHANNEL,
        fields=(SourceFieldId.ARBITRARY_SELECTION,),
        activation_any=(),
        effect=SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    return replace(
        base,
        features=(
            arbitrary,
            replace(
                basic,
                profile=replace(
                    basic.profile,
                    waveform_kinds=(
                        SourceWaveformKind.ARBITRARY,
                        SourceWaveformKind.SINE,
                    ),
                ),
            ),
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
            facets=(arbitrary_query, *base.query_contract.facets),
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
    dual_contract: bool = False,
    volatile: bool = False,
    volatile_write_error: bool = False,
    playback_modes: tuple[SourceArbitraryPlaybackMode, ...] = (
        SourceArbitraryPlaybackMode.DDS,
        SourceArbitraryPlaybackMode.TRUE_ARB,
    ),
) -> tuple[SourceService, _ArbitraryWriteDriver]:
    session_state = InstrumentSessionState(epoch_id="source-arbitrary-v2")
    driver = _ArbitraryWriteDriver(
        session_state=session_state,
        output_enabled=output_enabled,
        postcondition_mismatch=postcondition_mismatch,
        volatile_write_error=volatile_write_error,
    )
    capabilities = [
        "source.snapshot_v2",
        "source.arbitrary_storage_v2",
        "source.arbitrary_select_v2",
        "source.output_v2",
    ]
    if dual_contract:
        capabilities.append("source.arbitrary_upload")
    if volatile:
        capabilities.append("source.arbitrary_volatile_replace_v2")
    descriptor = replace(
        source_descriptor(driver=driver, extensions=_extensions(playback_modes=playback_modes)),
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


def _storage_request(
    payload: bytes,
    *,
    mode: SourceStorageWriteMode = SourceStorageWriteMode.CREATE_ONLY,
    expected_previous_sha256: str | None = None,
) -> SourceArbitraryStorageRequest:
    return SourceArbitraryStorageRequest(
        channel=1,
        slot_id="slot_a",
        write_mode=mode,
        payload_sha256=_digest(payload),
        payload_size_bytes=len(payload),
        expected_previous_sha256=expected_previous_sha256,
    )


def _volatile_request(payload: bytes) -> SourceArbitraryVolatileReplaceRequest:
    return SourceArbitraryVolatileReplaceRequest(
        channel=1,
        payload_sha256=_digest(payload),
        payload_size_bytes=len(payload),
        point_count=len(payload) // 2,
    )


def test_arbitrary_storage_v2_writes_once_without_selecting_or_disabling_output() -> None:
    service, driver = _service(output_enabled=True)
    payload = b"raw-arbitrary-storage-payload-must-not-leak"
    request = _storage_request(payload)

    result, artifact = service.mutate_arbitrary_storage_v2(
        request,
        payload=payload,
        correlation_id="arb-storage",
    )

    assert result.payload_sha256 == _digest(payload)
    assert driver.storage_requests == [(request, payload)]
    assert driver.select_requests == []
    assert driver.output_requests == []
    assert driver.output_enabled is True
    assert driver.transport.counters.binary_write_completed == 1
    assert artifact["operation"] == "source.arbitrary_storage_v2"
    assert artifact["request"] == {
        "type": "SourceArbitraryStorageRequest",
        "channel": 1,
        "slot_id": "slot_a",
        "write_mode": "create_only",
        "payload_sha256": _digest(payload),
        "payload_size_bytes": len(payload),
        "expected_previous_sha256": None,
    }
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "unchanged",
        "selection_expected": "unchanged",
        "storage_readback_verified": True,
    }
    assert payload.decode("ascii") not in repr(artifact)
    assert [item["phase"] for item in artifact["phases"]] == [
        "preflight",
        "main",
        "postcondition",
    ]


def test_arbitrary_storage_v2_rejects_create_and_cas_conflicts_before_binary_write() -> None:
    service, driver = _service()
    driver.slots["slot_a"] = b"old"

    with pytest.raises(ConfigError, match="target slot is not empty"):
        service.mutate_arbitrary_storage_v2(
            _storage_request(b"new"),
            payload=b"new",
        )
    with pytest.raises(ConfigError, match="expected previous storage digest"):
        service.mutate_arbitrary_storage_v2(
            _storage_request(
                b"new",
                mode=SourceStorageWriteMode.REPLACE_IF_DIGEST_MATCHES,
                expected_previous_sha256=_digest(b"different"),
            ),
            payload=b"new",
        )

    assert driver.storage_requests == []
    assert driver.transport.counters.binary_write_requests == 0


def test_arbitrary_storage_v2_checks_payload_before_opening_or_io() -> None:
    service, driver = _service()
    request = _storage_request(b"abc")

    with pytest.raises(ConfigError, match="SHA-256"):
        service.mutate_arbitrary_storage_v2(request, payload=b"abd")
    with pytest.raises(ConfigError, match="must be bytes"):
        service.mutate_arbitrary_storage_v2(request, payload=bytearray(b"abc"))  # type: ignore[arg-type]

    assert driver.transport.counters.binary_write_requests == 0
    assert driver.transport.counters.query_calls == 0


def test_arbitrary_select_v2_writes_once_and_keeps_target_output_off() -> None:
    service, driver = _service()
    driver.slots["slot_a"] = b"abc"
    request = SourceArbitrarySelectRequest(
        channel=1,
        slot_id="slot_a",
        playback_mode=SourceArbitraryPlaybackMode.DDS,
        playback_frequency_hz=1_000.0,
    )

    result, artifact = service.select_arbitrary_v2(request, correlation_id="arb-select")

    assert result.basic.waveform_kind.value is SourceWaveformKind.ARBITRARY
    assert result.arbitrary.selected_waveform_id.value == "slot_a"
    assert driver.select_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.arbitrary_select_v2"
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "off",
    }


def test_arbitrary_select_v2_requires_off_and_runtime_mode_before_write() -> None:
    request = SourceArbitrarySelectRequest(
        channel=1,
        slot_id="slot_a",
        playback_mode=SourceArbitraryPlaybackMode.TRUE_ARB,
        sample_rate_hz=10_000.0,
    )
    service, driver = _service(output_enabled=True)
    driver.slots["slot_a"] = b"abc"

    with pytest.raises(ConfigError, match="target output OFF"):
        service.select_arbitrary_v2(request)

    supported, supported_driver = _service(playback_modes=(SourceArbitraryPlaybackMode.DDS,))
    supported_driver.slots["slot_a"] = b"abc"
    with pytest.raises(ConfigError, match="playback mode is not supported"):
        supported.select_arbitrary_v2(request)

    assert driver.select_requests == []
    assert driver.transport.counters.write_requests == 0
    assert supported_driver.select_requests == []
    assert supported_driver.transport.counters.write_requests == 0


def test_arbitrary_select_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _service(postcondition_mismatch=True)
    driver.slots["slot_a"] = b"abc"
    request = SourceArbitrarySelectRequest(
        channel=1,
        slot_id="slot_a",
        playback_mode=SourceArbitraryPlaybackMode.DDS,
        playback_frequency_hz=1_000.0,
    )

    with pytest.raises(ConfigError, match="selected waveform readback") as raised:
        service.select_arbitrary_v2(request)

    artifact = raised.value.source_operation_artifact
    assert driver.select_requests == [request]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }


def test_arbitrary_volatile_replace_v2_writes_once_and_keeps_output_off() -> None:
    service, driver = _service(volatile=True)
    payload = b"\x00\x00\xff\x3f"
    request = _volatile_request(payload)

    result, artifact = service.replace_arbitrary_volatile_v2(
        request,
        payload=payload,
        correlation_id="arb-volatile",
    )

    assert driver.volatile_requests == [(request, payload)]
    assert driver.output_requests == []
    assert driver.output_enabled is False
    assert driver.transport.counters.binary_write_completed == 1
    assert result.content_readback_verified is False
    assert result.previous_content_restorable is False
    assert artifact["operation"] == "source.arbitrary_volatile_replace_v2"
    assert artifact["request"] == {
        "type": "SourceArbitraryVolatileReplaceRequest",
        "channel": 1,
        "payload_sha256": _digest(payload),
        "payload_size_bytes": len(payload),
        "point_count": 2,
    }
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "off",
        "selection_expected": "volatile",
        "content_readback_verified": False,
        "previous_content": "unrecoverable",
    }
    assert payload.hex() not in repr(artifact)
    assert [item["phase"] for item in artifact["phases"]] == [
        "preflight",
        "main",
        "postcondition",
    ]


def test_arbitrary_volatile_replace_v2_rejects_invalid_payload_and_preflight_before_write() -> None:
    payload = b"\x00\x00\xff\x3f"
    request = _volatile_request(payload)
    service, driver = _service(volatile=True)

    with pytest.raises(ConfigError, match="SHA-256"):
        service.replace_arbitrary_volatile_v2(request, payload=b"\x00\x00\x00\x00")
    with pytest.raises(ConfigError, match="must be bytes"):
        service.replace_arbitrary_volatile_v2(
            request,
            payload=bytearray(payload),  # type: ignore[arg-type]
        )
    assert driver.transport.counters.binary_write_requests == 0
    assert driver.transport.counters.query_calls == 0

    output_on, output_on_driver = _service(output_enabled=True, volatile=True)
    with pytest.raises(ConfigError, match="target output OFF"):
        output_on.replace_arbitrary_volatile_v2(request, payload=payload)

    below_minimum = SourceArbitraryVolatileReplaceRequest(
        1,
        _digest(b"\x00\x00"),
        2,
        1,
    )
    with pytest.raises(ConfigError, match="point count exceeds"):
        service.replace_arbitrary_volatile_v2(below_minimum, payload=b"\x00\x00")

    assert driver.volatile_requests == []
    assert driver.transport.counters.binary_write_requests == 0
    assert output_on_driver.volatile_requests == []
    assert output_on_driver.transport.counters.binary_write_requests == 0


@pytest.mark.parametrize(
    ("postcondition_mismatch", "volatile_write_error", "message"),
    (
        (True, False, "selected waveform readback"),
        (False, True, "volatile binary write result is unknown"),
    ),
)
def test_arbitrary_volatile_replace_v2_failure_runs_one_off_recovery(
    postcondition_mismatch: bool,
    volatile_write_error: bool,
    message: str,
) -> None:
    service, driver = _service(
        volatile=True,
        postcondition_mismatch=postcondition_mismatch,
        volatile_write_error=volatile_write_error,
    )
    payload = b"\x00\x00\xff\x3f"
    request = _volatile_request(payload)

    with pytest.raises(ConfigError, match=message) as raised:
        service.replace_arbitrary_volatile_v2(request, payload=payload)

    artifact = raised.value.source_operation_artifact
    assert driver.volatile_requests == [(request, payload)]
    assert driver.transport.counters.binary_write_requests == 1
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }
    assert artifact["final_state"]["previous_content"] == "unrecoverable"


def test_v1_arbitrary_upload_rejects_before_loading_file_or_io_for_dual_contract_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.upload_arbitrary_waveform(
            channel=1,
            file_path="does-not-exist.csv",
            playback_frequency_hz=1_000.0,
            amplitude_vpp=1.0,
        )

    assert driver.v1_upload_calls == 0
    assert driver.transport.counters.binary_write_requests == 0
    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0
