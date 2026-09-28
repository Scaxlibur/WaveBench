from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from wavebench.errors import ConfigError, DataError, SessionHealthError, TransportIOError
from wavebench.instruments import (
    InstrumentDescriptor,
    ScopeMeasurementSelector,
    ScopeMeasurementStatisticsDriverV2,
    ScopeMeasurementStatisticsRequestV2,
    ScopeMeasurementStatisticsV2,
)
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.contracts import ScopeMeasurementStatisticsDriver
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import ScopeMeasurementStatistics
from wavebench.instruments.scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    SCOPE_STRICT_V2_CAPABILITIES,
    validate_scope_descriptor,
)
from wavebench.instruments.scope_extensions import (
    ScopeDescriptorExtensions,
    ScopeMeasurementStatisticsProfileV2,
)
from wavebench.logging import CommandLogger
from wavebench.services.operation_specs import (
    SCOPE_OPERATION_SPECS,
    SCOPE_PORTABILITY_V2_OPERATION_SPECS,
    require_operation_spec,
)
from wavebench.services.scope_service import ScopeService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport


def _slot_selector(slot: int = 1) -> ScopeMeasurementSelector:
    return ScopeMeasurementSelector(slot=slot)


def _item_selector() -> ScopeMeasurementSelector:
    return ScopeMeasurementSelector(item="VPP", sources=("CH1",))


def _slot_request(slot: int = 1, *, include_buffer: bool = False) -> ScopeMeasurementStatisticsRequestV2:
    return ScopeMeasurementStatisticsRequestV2(
        selector=_slot_selector(slot),
        configured=True,
        include_buffer=include_buffer,
    )


def _item_request() -> ScopeMeasurementStatisticsRequestV2:
    return ScopeMeasurementStatisticsRequestV2(
        selector=_item_selector(),
        configured=True,
    )


def _result(
    request: ScopeMeasurementStatisticsRequestV2,
) -> ScopeMeasurementStatisticsV2:
    return ScopeMeasurementStatisticsV2(
        selector=request.selector,
        category="VOLTAGE",
        actual=1.0,
        average=1.1,
        standard_deviation=0.1,
        minimum=0.9,
        maximum=1.2,
        waveform_count=16,
    )


def _slot_profile(max_queries: int = 1) -> ScopeMeasurementStatisticsProfileV2:
    return ScopeMeasurementStatisticsProfileV2(
        selector_modes=("slot",),
        max_queries=max_queries,
        slot_range=(1, 4),
    )


def _item_profile(max_queries: int = 6) -> ScopeMeasurementStatisticsProfileV2:
    return ScopeMeasurementStatisticsProfileV2(
        selector_modes=("item_sources",),
        max_queries=max_queries,
        supported_items=("VPP", "VRMS"),
        item_source_count_range=(1, 2),
    )


def _descriptor(
    *,
    profile: ScopeMeasurementStatisticsProfileV2 | None = None,
    minimum: str = "0.8.24",
    capabilities: tuple[str, ...] = (),
    extensions: bool = True,
) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        driver_id="example.statistics-v2",
        kind="scope",
        display_name="Example scope",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities or ("scope.measurement_statistics_v2",),
        idn_patterns=("EXAMPLE,EX1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda _context: object(),
        wavebench_min_version=minimum,
        scope_extensions=(
            ScopeDescriptorExtensions(
                measurement_statistics_profile_v2=profile or _slot_profile(),
            )
            if extensions
            else None
        ),
    )


def test_measurement_statistics_v2_models_preserve_slot_and_item_source_selectors() -> None:
    slot_request = _slot_request(2)
    item_request = _item_request()
    result = _result(item_request)

    assert slot_request.selector.mode == "slot"
    assert item_request.selector.mode == "item_sources"
    assert asdict(result) == {
        "selector": {"slot": None, "item": "VPP", "sources": ("CH1",)},
        "category": "VOLTAGE",
        "actual": 1.0,
        "average": 1.1,
        "standard_deviation": 0.1,
        "minimum": 0.9,
        "maximum": 1.2,
        "waveform_count": 16,
        "buffered_values": None,
    }


@pytest.mark.parametrize(
    ("factory", "error_type", "message"),
    (
        (lambda: ScopeMeasurementSelector(), ValueError, "safe item token"),
        (lambda: ScopeMeasurementSelector(slot=True), ValueError, "positive integer"),
        (lambda: ScopeMeasurementSelector(slot=1, item="VPP"), ValueError, "cannot include"),
        (lambda: ScopeMeasurementSelector(item="VPP", sources=()), ValueError, "at least one"),
        (
            lambda: ScopeMeasurementSelector(item="VPP", sources=("CH1", "CH1")),
            ValueError,
            "unique",
        ),
        (
            lambda: ScopeMeasurementStatisticsRequestV2(
                selector=_slot_selector(),
                configured=False,
            ),
            ValueError,
            "configured=True",
        ),
        (
            lambda: ScopeMeasurementStatisticsRequestV2(
                selector=_slot_selector(),
                configured=True,
                include_buffer=1,  # type: ignore[arg-type]
            ),
            TypeError,
            "include_buffer",
        ),
        (
            lambda: ScopeMeasurementStatisticsV2(
                selector=_slot_selector(),
                category="VOLTAGE",
                actual=float("nan"),
                average=1.0,
                standard_deviation=0.0,
                minimum=1.0,
                maximum=1.0,
                waveform_count=1,
            ),
            ValueError,
            "actual",
        ),
        (
            lambda: ScopeMeasurementStatisticsV2(
                selector=_slot_selector(),
                category="VOLTAGE",
                actual=1.0,
                average=1.0,
                standard_deviation=0.0,
                minimum=1.0,
                maximum=1.0,
                waveform_count=True,
            ),
            ValueError,
            "waveform_count",
        ),
    ),
)
def test_measurement_statistics_v2_models_reject_ambiguous_or_partial_values(
    factory,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        factory()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"selector_modes": ()}, "invalid"),
        ({"selector_modes": ("item_sources", "slot")}, "stable mode order"),
        ({"selector_modes": ("slot",), "slot_range": None}, "two-integer"),
            (
                {
                    "selector_modes": ("item_sources",),
                    "slot_range": None,
                    "supported_items": (),
                },
                "requires supported_items",
            ),
            (
                {
                    "selector_modes": ("item_sources",),
                    "slot_range": None,
                    "supported_items": ("VPP",),
                    "item_source_count_range": None,
            },
            "two-integer",
        ),
        ({"supports_buffer": True}, "does not support"),
        ({"max_queries": True}, "integer"),
        ({"max_queries": 33}, "1..32"),
    ),
)
def test_measurement_statistics_v2_profile_rejects_invalid_static_contract(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "selector_modes": ("slot",),
        "max_queries": 1,
        "slot_range": (1, 4),
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError), match=message):
        ScopeMeasurementStatisticsProfileV2(**values)  # type: ignore[arg-type]


def test_measurement_statistics_v2_profile_preflights_selectors_and_rejects_buffers() -> None:
    profile = _item_profile()

    profile.validate_request(_item_request())
    with pytest.raises(ValueError, match="selector mode"):
        profile.validate_request(_slot_request())
    with pytest.raises(ValueError, match="outside the descriptor profile"):
        profile.validate_request(
            ScopeMeasurementStatisticsRequestV2(
                selector=ScopeMeasurementSelector(item="FREQ", sources=("CH1",)),
                configured=True,
            )
        )
    with pytest.raises(ValueError, match="does not support statistics buffers"):
        _slot_profile().validate_request(_slot_request(include_buffer=True))


def test_measurement_statistics_v2_profile_requires_result_selector_echo_and_no_buffer() -> None:
    request = _item_request()
    profile = _item_profile()

    profile.validate_result(_result(request), request=request)
    with pytest.raises(ValueError, match="does not match request"):
        profile.validate_result(_result(_slot_request()), request=request)
    with pytest.raises(ValueError, match="must not include a buffer"):
        profile.validate_result(
            replace(_result(request), buffered_values=(1.0,)),
            request=request,
        )


def test_measurement_statistics_v2_is_additive_and_registered_without_r13_membership() -> None:
    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_measurement_statistics_v2(
            self,
            request: ScopeMeasurementStatisticsRequestV2,
        ) -> ScopeMeasurementStatisticsV2:
            return _result(request)

    assert isinstance(Driver(), ScopeMeasurementStatisticsDriverV2)
    assert "get_measurement_statistics_v2" not in ScopeMeasurementStatisticsDriver.__dict__
    assert SCOPE_CAPABILITY_METHODS["scope.measurement_statistics_v2"] == (
        "get_measurement_statistics_v2",
    )
    assert CAPABILITY_METHODS["scope.measurement_statistics_v2"] == (
        "get_measurement_statistics_v2",
    )
    assert "scope.measurement_statistics_v2" in SCOPE_STRICT_V2_CAPABILITIES
    assert "scope.measurement_statistics_v2" not in SCOPE_OPERATION_SPECS
    spec = SCOPE_PORTABILITY_V2_OPERATION_SPECS["scope.measurement_statistics_v2"]
    assert require_operation_spec("scope.measurement_statistics_v2") is spec
    assert spec.effect == "stateful_read"
    assert spec.lease_mode == "exclusive"
    assert spec.required_verified_fields == ()
    assert spec.error_check_minimum == "disabled"


def test_measurement_statistics_v2_descriptor_requires_profile_core_floor_and_callable_method() -> None:
    with pytest.raises(ConfigError, match="scope portability V2 capabilities require.*0.8.24"):
        validate_scope_descriptor(_descriptor(minimum="0.8.23"))
    with pytest.raises(ConfigError, match="measurement_statistics_profile_v2"):
        validate_scope_descriptor(_descriptor(extensions=False))

    class MissingMethod:
        def close(self) -> None:
            pass

    with pytest.raises(TypeError, match="get_measurement_statistics_v2"):
        validate_declared_capabilities(_descriptor(), MissingMethod())


def test_measurement_statistics_v2_method_does_not_create_an_implicit_capability() -> None:
    descriptor = _descriptor(capabilities=("scope.idn",), extensions=False)

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_measurement_statistics_v2(
            self,
            request: ScopeMeasurementStatisticsRequestV2,
        ) -> ScopeMeasurementStatisticsV2:
            raise AssertionError(f"undeclared V2 method must not be called for {request!r}")

    validate_declared_capabilities(descriptor, Driver())
    assert "scope.measurement_statistics_v2" not in descriptor.capabilities


def _service(
    *,
    profile: ScopeMeasurementStatisticsProfileV2,
    driver: object,
    transport: GuardedAuditedTransport | None = None,
) -> ScopeService:
    return ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.statistics-v2"),
            connection=SimpleNamespace(timeout_ms=1_000),
        ),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.statistics-v2",
            capabilities=("scope.measurement_statistics_v2",),
            scope_extensions=ScopeDescriptorExtensions(
                measurement_statistics_profile_v2=profile,
            ),
        ),
        transport=transport,
        session_state=None if transport is None else transport.session_state,
    )


def test_measurement_statistics_v2_service_only_calls_v2_and_keeps_legacy_route_separate() -> None:
    request = _slot_request()
    calls: list[str] = []
    driver = SimpleNamespace(
        get_measurement_statistics_v2=lambda received: calls.append("v2")
        or _result(received),
        get_measurement_statistics=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy statistics must stay separate")
        ),
    )
    service = _service(profile=_slot_profile(), driver=driver)

    assert service.measurement_statistics_v2(request) == _result(request)
    assert calls == ["v2"]

    legacy = ScopeMeasurementStatistics(
        slot=1,
        category="VOLTAGE",
        actual=None,
        average=None,
        standard_deviation=None,
        minimum=None,
        maximum=None,
        waveform_count=0,
    )
    driver.get_measurement_statistics = lambda *_args, **_kwargs: calls.append("legacy") or legacy
    service.descriptor = SimpleNamespace(
        driver_id="example.statistics-v2",
        capabilities=("scope.measurement_statistics", "scope.measurement_statistics_v2"),
        scope_extensions=ScopeDescriptorExtensions(
            measurement_statistics_profile_v2=_slot_profile(),
        ),
    )
    assert service.measurement_statistics(1, configured_slot=True) is legacy
    assert calls == ["v2", "legacy"]


def test_measurement_statistics_v2_profile_rejects_before_opening_scope() -> None:
    service = _service(
        profile=_slot_profile(),
        driver=SimpleNamespace(get_measurement_statistics_v2=lambda request: _result(request)),
    )
    service._open_scope = lambda: pytest.fail("unsupported V2 request must not open scope")

    with pytest.raises(ConfigError, match="selector mode"):
        service.measurement_statistics_v2(_item_request())
    with pytest.raises(ConfigError, match="does not support statistics buffers"):
        service.measurement_statistics_v2(_slot_request(include_buffer=True))


def test_measurement_statistics_v2_service_rejects_invalid_result_without_fallback() -> None:
    request = _slot_request()
    service = _service(
        profile=_slot_profile(),
        driver=SimpleNamespace(get_measurement_statistics_v2=lambda _request: object()),
    )

    with pytest.raises(DataError, match="invalid result"):
        service.measurement_statistics_v2(request)

    service.session = SimpleNamespace(
        get_measurement_statistics_v2=lambda received: replace(
            _result(received),
            buffered_values=(1.0,),
        )
    )
    with pytest.raises(DataError, match="must not include a buffer"):
        service.measurement_statistics_v2(request)


class _InnerTransport:
    resource = "TCPIP::example::INSTR"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.writes: list[str] = []

    def record_event(self, _direction: str, _text: str) -> None:
        pass

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        assert replay is ReplayPolicy.NO_REPLAY
        self.queries.append(command)
        return "1.0"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def close(self) -> None:
        pass


def test_measurement_statistics_v2_uses_only_budgeted_text_queries_without_identity_preflight() -> None:
    request = _slot_request()
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)

    class Driver:
        def idn(self) -> str:
            raise AssertionError("statistics V2 must not call legacy identity preflight")

        def get_measurement_statistics_v2(
            self,
            received: ScopeMeasurementStatisticsRequestV2,
        ) -> ScopeMeasurementStatisticsV2:
            assert received == request
            transport.query("MEAS:CURRENT?")
            return _result(received)

    service = _service(profile=_slot_profile(max_queries=1), driver=Driver(), transport=transport)

    assert service.measurement_statistics_v2(request) == _result(request)
    assert inner.queries == ["MEAS:CURRENT?"]
    assert inner.writes == []
    assert transport.counters.query_calls == 1


@pytest.mark.parametrize("mode", ("overrun", "write"))
def test_measurement_statistics_v2_rejects_overrun_and_non_query_io_before_backend_send(
    mode: str,
) -> None:
    request = _slot_request()
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)

    class Driver:
        def get_measurement_statistics_v2(
            self,
            received: ScopeMeasurementStatisticsRequestV2,
        ) -> ScopeMeasurementStatisticsV2:
            assert received == request
            transport.query("MEAS:CURRENT?")
            if mode == "overrun":
                transport.query("MEAS:EXTRA?")
            else:
                transport.write("MEAS:BAD")
            return _result(received)

    service = _service(profile=_slot_profile(max_queries=1), driver=Driver(), transport=transport)

    with pytest.raises(SessionHealthError):
        service.measurement_statistics_v2(request)
    assert inner.queries == ["MEAS:CURRENT?"]
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
        driver_reference="example.statistics-v2",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )


def test_measurement_statistics_v2_factory_latch_blocks_construction_io_until_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()
    errors: list[TransportIOError] = []

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_measurement_statistics_v2(
            self,
            request: ScopeMeasurementStatisticsRequestV2,
        ) -> ScopeMeasurementStatisticsV2:
            return _result(request)

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


def test_measurement_statistics_v2_factory_closes_missing_method_without_io(
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

    with pytest.raises(ConfigError, match="get_measurement_statistics_v2"):
        _open_factory_descriptor()

    assert inner.queries == []
    assert inner.closed == 1


def test_undeclared_measurement_statistics_v2_method_does_not_latch_legacy_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()

    class LegacyDriver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_measurement_statistics_v2(
            self,
            request: ScopeMeasurementStatisticsRequestV2,
        ) -> ScopeMeasurementStatisticsV2:
            raise AssertionError(f"undeclared statistics V2 must not be called for {request!r}")

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
