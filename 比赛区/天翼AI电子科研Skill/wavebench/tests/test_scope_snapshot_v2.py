from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from wavebench.errors import ConfigError, DataError, SessionHealthError, TransportIOError
from wavebench.instruments import (
    InstrumentDescriptor,
    SCOPE_SNAPSHOT_V2_FIELD_ORDER,
    ScopeAnalogChannelSnapshotV2,
    ScopeHealthSnapshotV2,
    ScopeIdentitySnapshot,
    ScopeSnapshotDriverV2,
    ScopeSnapshotV2,
)
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.contracts import ScopeSnapshotDriver
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    SCOPE_STRICT_V2_CAPABILITIES,
    validate_scope_descriptor,
)
from wavebench.instruments.scope_extensions import (
    ScopeDescriptorExtensions,
    ScopeSnapshotProfileV2,
)
from wavebench.logging import CommandLogger
from wavebench.services.capability_explain import explain_operation
from wavebench.services.operation_specs import (
    SCOPE_OPERATION_SPECS,
    SCOPE_PORTABILITY_V2_OPERATION_SPECS,
    require_operation_spec,
)
from wavebench.services.scope_service import ScopeService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport


_IDENTITY_FIELDS = (
    "identity.manufacturer",
    "identity.model",
    "identity.serial_number",
    "identity.firmware",
    "identity.options",
)


def _profile(
    *,
    readable_fields: tuple[str, ...] = _IDENTITY_FIELDS,
    max_queries: int = 1,
    conditionally_applicable_fields: tuple[str, ...] = (),
) -> ScopeSnapshotProfileV2:
    return ScopeSnapshotProfileV2(
        readable_fields=readable_fields,  # type: ignore[arg-type]
        max_queries=max_queries,
        conditionally_applicable_fields=conditionally_applicable_fields,  # type: ignore[arg-type]
    )


def _descriptor(
    *,
    profile: ScopeSnapshotProfileV2 | None = None,
    minimum: str = "0.8.24",
    capabilities: tuple[str, ...] = (),
    extensions: bool = True,
) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        driver_id="example.snapshot-v2",
        kind="scope",
        display_name="Example scope",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities or ("scope.snapshot_v2",),
        idn_patterns=("EXAMPLE,EX1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda _context: object(),
        wavebench_min_version=minimum,
        scope_extensions=(
            ScopeDescriptorExtensions(snapshot_profile_v2=profile or _profile())
            if extensions
            else None
        ),
    )


def _identity() -> ScopeIdentitySnapshot:
    return ScopeIdentitySnapshot(
        manufacturer="Example",
        model="EX1",
        serial_number="SN-1",
        firmware="1.0",
        options=("OPT1",),
    )


def _unavailable(*available: str, not_applicable: tuple[str, ...] = ()) -> tuple[str, ...]:
    available_set = set(available)
    not_applicable_set = set(not_applicable)
    return tuple(
        field_name
        for field_name in SCOPE_SNAPSHOT_V2_FIELD_ORDER
        if field_name not in available_set and field_name not in not_applicable_set
    )


def _identity_only_snapshot(channel: int = 1) -> ScopeSnapshotV2:
    del channel
    return ScopeSnapshotV2(
        identity=_identity(),
        unavailable_fields=_unavailable(*_IDENTITY_FIELDS),
    )


def _snapshot_with_health(
    *,
    sample_rate_hz: float | None,
    waiting_for_trigger: bool | None = None,
) -> ScopeSnapshotV2:
    available = (*_IDENTITY_FIELDS, "health.sample_rate_hz")
    not_applicable = (
        ("health.waiting_for_trigger",) if waiting_for_trigger is None else ()
    )
    if sample_rate_hz is None:
        available = _IDENTITY_FIELDS
    return ScopeSnapshotV2(
        identity=_identity(),
        health=ScopeHealthSnapshotV2(
            sample_rate_hz=sample_rate_hz,
            waiting_for_trigger=waiting_for_trigger,
        ),
        unavailable_fields=_unavailable(*available, not_applicable=not_applicable),
        not_applicable_fields=not_applicable,  # type: ignore[arg-type]
    )


def test_snapshot_v2_model_serializes_identity_only_and_partial_health() -> None:
    identity_only = _identity_only_snapshot()
    partial = _snapshot_with_health(sample_rate_hz=1_000_000.0)

    assert asdict(identity_only)["identity"]["model"] == "EX1"
    assert asdict(identity_only)["health"] is None
    assert identity_only.unavailable_fields == _unavailable(*_IDENTITY_FIELDS)
    assert partial.health is not None
    assert partial.health.sample_rate_hz == 1_000_000.0
    assert "health.sample_rate_hz" not in partial.unavailable_fields
    assert partial.not_applicable_fields == ("health.waiting_for_trigger",)


@pytest.mark.parametrize(
    ("factory", "error_type", "message"),
    (
        (
            lambda: ScopeAnalogChannelSnapshotV2(channel=True),
            ValueError,
            "positive integer",
        ),
        (
            lambda: ScopeHealthSnapshotV2(sample_rate_hz=float("nan")),
            ValueError,
            "finite",
        ),
        (
            lambda: ScopeSnapshotV2(
                identity=_identity(),
                unavailable_fields=tuple(reversed(_unavailable(*_IDENTITY_FIELDS))),  # type: ignore[arg-type]
            ),
            ValueError,
            "stable field order",
        ),
        (
            lambda: ScopeSnapshotV2(
                identity=_identity(),
                unavailable_fields=_unavailable(*_IDENTITY_FIELDS),
                not_applicable_fields=("health.status_byte",),
            ),
            ValueError,
            "mutually exclusive",
        ),
    ),
)
def test_snapshot_v2_models_reject_invalid_or_ambiguous_values(
    factory,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        factory()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"readable_fields": ()}, "must contain"),
        ({"readable_fields": _IDENTITY_FIELDS[:-1]}, "include all identity"),
        (
            {
                "readable_fields": (*_IDENTITY_FIELDS, "channel.enabled"),
            },
            "require 'channel.channel'",
        ),
        (
            {
                "conditionally_applicable_fields": ("identity.model",),
            },
            "cannot be conditional",
        ),
        ({"max_queries": True}, "integer"),
    ),
)
def test_snapshot_v2_profile_rejects_invalid_static_contract(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "readable_fields": _IDENTITY_FIELDS,
        "max_queries": 1,
        "conditionally_applicable_fields": (),
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError), match=message):
        ScopeSnapshotProfileV2(**values)  # type: ignore[arg-type]


def test_snapshot_v2_profile_enforces_readable_and_conditional_result_contract() -> None:
    profile = _profile(
        readable_fields=(*_IDENTITY_FIELDS, "health.sample_rate_hz", "health.waiting_for_trigger"),
        max_queries=2,
        conditionally_applicable_fields=("health.waiting_for_trigger",),
    )
    result = _snapshot_with_health(sample_rate_hz=1_000_000.0)

    profile.validate_result(result, channel=1)

    with pytest.raises(ValueError, match="non-conditional readable fields"):
        profile.validate_result(_identity_only_snapshot(), channel=1)


def test_snapshot_v2_is_additive_and_registered_without_r13_membership() -> None:
    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_snapshot_v2(
            self,
            channel: int,
            *,
            fields: tuple[str, ...],
        ) -> ScopeSnapshotV2:
            assert channel == 1
            assert fields == _IDENTITY_FIELDS
            return _identity_only_snapshot()

    assert isinstance(Driver(), ScopeSnapshotDriverV2)
    assert "get_snapshot_v2" not in ScopeSnapshotDriver.__dict__
    assert SCOPE_CAPABILITY_METHODS["scope.snapshot_v2"] == ("get_snapshot_v2",)
    assert CAPABILITY_METHODS["scope.snapshot_v2"] == ("get_snapshot_v2",)
    assert "scope.snapshot_v2" in SCOPE_STRICT_V2_CAPABILITIES
    assert "scope.snapshot_v2" not in SCOPE_OPERATION_SPECS
    spec = SCOPE_PORTABILITY_V2_OPERATION_SPECS["scope.snapshot_v2"]
    assert require_operation_spec("scope.snapshot_v2") is spec
    assert spec.effect == "stateful_read"
    assert spec.lease_mode == "exclusive"
    assert spec.required_verified_fields == ()
    assert spec.error_check_minimum == "disabled"


def test_snapshot_v2_descriptor_requires_profile_core_floor_and_callable_method() -> None:
    with pytest.raises(ConfigError, match="scope portability V2 capabilities require.*0.8.24"):
        validate_scope_descriptor(_descriptor(minimum="0.8.23"))
    with pytest.raises(ConfigError, match="snapshot_profile_v2"):
        validate_scope_descriptor(_descriptor(extensions=False))

    class MissingMethod:
        def close(self) -> None:
            pass

    with pytest.raises(TypeError, match="get_snapshot_v2"):
        validate_declared_capabilities(_descriptor(), MissingMethod())


def test_snapshot_v2_method_does_not_create_an_implicit_capability() -> None:
    descriptor = _descriptor(capabilities=("scope.idn",), extensions=False)

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_snapshot_v2(
            self,
            _channel: int,
            *,
            fields: tuple[str, ...],
        ) -> ScopeSnapshotV2:
            raise AssertionError(f"undeclared snapshot V2 must not be called for {fields!r}")

    validate_declared_capabilities(descriptor, Driver())
    assert "scope.snapshot_v2" not in descriptor.capabilities


def test_snapshot_v2_service_only_calls_v2_and_keeps_legacy_status_separate() -> None:
    calls: list[tuple[int, tuple[str, ...]]] = []
    driver = SimpleNamespace(
        get_snapshot_v2=lambda channel, *, fields: calls.append((channel, fields))
        or _identity_only_snapshot(channel),
        get_snapshot=lambda _channel: (_ for _ in ()).throw(
            AssertionError("legacy get_snapshot must not be called")
        ),
    )
    profile = _profile()
    service = ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.snapshot-v2"),
            connection=SimpleNamespace(timeout_ms=1_000),
        ),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.snapshot-v2",
            capabilities=("scope.snapshot_v2",),
            scope_extensions=ScopeDescriptorExtensions(snapshot_profile_v2=profile),
        ),
    )

    assert service.snapshot_v2(1) == _identity_only_snapshot(1)
    assert calls == [(1, _IDENTITY_FIELDS)]


def test_snapshot_v2_does_not_change_legacy_status_route() -> None:
    legacy = object()
    calls: list[tuple[str, int]] = []
    driver = SimpleNamespace(
        get_snapshot=lambda channel: calls.append(("legacy", channel)) or legacy,
        get_snapshot_v2=lambda channel, *, fields: calls.append(("v2", channel))
        or _identity_only_snapshot(channel),
    )
    service = ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.snapshot-v2"),
            connection=SimpleNamespace(timeout_ms=1_000),
        ),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.snapshot-v2",
            capabilities=("scope.snapshot", "scope.snapshot_v2"),
            scope_extensions=ScopeDescriptorExtensions(snapshot_profile_v2=_profile()),
        ),
    )

    assert service.status(2) is legacy
    assert calls == [("legacy", 2)]


def test_snapshot_v2_capability_explain_is_additive() -> None:
    explanation = explain_operation(
        "scope.snapshot_v2",
        descriptor=SimpleNamespace(
            driver_id="example.snapshot-v2",
            kind="scope",
            capabilities=("scope.snapshot_v2",),
        ),
    )

    assert explanation.status == "supported"
    assert explanation.spec is not None
    assert explanation.spec.required_verified_fields == ()


def test_snapshot_v2_service_rejects_invalid_channel_or_result_before_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()
    service = ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.snapshot-v2"),
            connection=SimpleNamespace(timeout_ms=1_000),
        ),
        logger=SimpleNamespace(),
        descriptor=SimpleNamespace(
            driver_id="example.snapshot-v2",
            capabilities=("scope.snapshot_v2",),
            scope_extensions=ScopeDescriptorExtensions(snapshot_profile_v2=profile),
        ),
    )
    monkeypatch.setattr(service, "_open_scope", lambda: pytest.fail("must not open"))
    with pytest.raises(ConfigError, match="positive integer"):
        service.snapshot_v2(0)

    service.session = SimpleNamespace(
        get_snapshot_v2=lambda _channel, *, fields: object(),
    )
    with pytest.raises(DataError, match="invalid result"):
        service.snapshot_v2(1)


class _InnerTransport:
    resource = "TCPIP::example::INSTR"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.writes: list[str] = []
        self.closed = 0

    def record_event(self, _direction: str, _text: str) -> None:
        pass

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        assert replay is ReplayPolicy.NO_REPLAY
        self.queries.append(command)
        return "EXAMPLE,EX1,SN-1,1.0"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def close(self) -> None:
        self.closed += 1


def _budgeted_service(
    *,
    profile: ScopeSnapshotProfileV2,
    driver: object,
    transport: GuardedAuditedTransport,
) -> ScopeService:
    return ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.snapshot-v2"),
            connection=SimpleNamespace(timeout_ms=1_000),
        ),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.snapshot-v2",
            capabilities=("scope.snapshot_v2",),
            scope_extensions=ScopeDescriptorExtensions(snapshot_profile_v2=profile),
        ),
        transport=transport,
        session_state=transport.session_state,
    )


def test_snapshot_v2_service_uses_only_budgeted_text_queries_without_identity_preflight() -> None:
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)

    class Driver:
        def idn(self) -> str:
            raise AssertionError("snapshot V2 must not call the legacy identity preflight")

        def get_snapshot_v2(
            self,
            _channel: int,
            *,
            fields: tuple[str, ...],
        ) -> ScopeSnapshotV2:
            assert fields == _IDENTITY_FIELDS
            transport.query("SNAP:IDN?")
            return _identity_only_snapshot()

    service = _budgeted_service(profile=_profile(max_queries=1), driver=Driver(), transport=transport)

    assert service.snapshot_v2(1) == _identity_only_snapshot()
    assert inner.queries == ["SNAP:IDN?"]
    assert inner.writes == []
    assert transport.counters.query_calls == 1


@pytest.mark.parametrize("mode", ("overrun", "write"))
def test_snapshot_v2_service_rejects_overrun_and_non_query_io_before_backend_send(mode: str) -> None:
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)

    class Driver:
        def get_snapshot_v2(
            self,
            _channel: int,
            *,
            fields: tuple[str, ...],
        ) -> ScopeSnapshotV2:
            assert fields == _IDENTITY_FIELDS
            transport.query("SNAP:IDN?")
            if mode == "overrun":
                transport.query("SNAP:EXTRA?")
            else:
                transport.write("SNAP:BAD")
            return _identity_only_snapshot()

    service = _budgeted_service(profile=_profile(max_queries=1), driver=Driver(), transport=transport)

    with pytest.raises(SessionHealthError):
        service.snapshot_v2(1)
    assert inner.queries == ["SNAP:IDN?"]
    assert inner.writes == []


class _FactoryTransport:
    resource = "TCPIP::example::INSTR"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.closed = 0

    def record_event(self, _direction: str, _text: str) -> None:
        pass

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        assert replay is ReplayPolicy.NO_REPLAY
        self.queries.append(command)
        return "ok"

    def close(self) -> None:
        self.closed += 1


def _open_factory_descriptor() -> object:
    return open_instrument_driver(
        driver_reference="example.snapshot-v2",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )


def test_snapshot_v2_factory_latch_blocks_construction_io_until_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()
    errors: list[TransportIOError] = []

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_snapshot_v2(
            self,
            _channel: int,
            *,
            fields: tuple[str, ...],
        ) -> ScopeSnapshotV2:
            assert fields == _IDENTITY_FIELDS
            return _identity_only_snapshot()

    def factory(context):
        transport = context.open_transport()
        with pytest.raises(TransportIOError) as raised:
            transport.query("*IDN?")
        errors.append(raised.value)
        return Driver()

    descriptor = replace(_descriptor(), factory=factory)
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda _reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **_kwargs: inner)

    opened = _open_factory_descriptor()

    assert [error.reason_code for error in errors] == ["factory_construction_pending"]
    assert errors[0].attempts == 0
    assert inner.queries == []
    assert opened.transport is not None
    assert opened.transport.query("*IDN?") == "ok"
    assert inner.queries == ["*IDN?"]


def test_snapshot_v2_factory_closes_missing_method_without_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()

    class MissingMethod:
        def close(self) -> None:
            pass

    descriptor = replace(
        _descriptor(),
        factory=lambda context: (context.open_transport(), MissingMethod())[1],
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda _reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **_kwargs: inner)

    with pytest.raises(ConfigError, match="get_snapshot_v2"):
        _open_factory_descriptor()

    assert inner.queries == []
    assert inner.closed == 1


def test_undeclared_snapshot_v2_method_does_not_latch_legacy_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()

    class LegacyDriver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_snapshot_v2(
            self,
            _channel: int,
            *,
            fields: tuple[str, ...],
        ) -> ScopeSnapshotV2:
            raise AssertionError(f"undeclared snapshot V2 must not be called for {fields!r}")

    descriptor = replace(
        _descriptor(capabilities=("scope.idn",), extensions=False),
        factory=lambda context: (context.open_transport().query("*IDN?"), LegacyDriver())[1],
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda _reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **_kwargs: inner)

    _open_factory_descriptor()

    assert inner.queries == ["*IDN?"]
