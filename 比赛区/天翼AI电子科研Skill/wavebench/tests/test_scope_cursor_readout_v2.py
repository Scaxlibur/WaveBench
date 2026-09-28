from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from wavebench.errors import ConfigError, DataError, SessionHealthError, TransportIOError
from wavebench.instruments import (
    InstrumentDescriptor,
    SCOPE_CURSOR_READOUT_V2_FIELD_ORDER,
    SCOPE_CURSOR_READOUT_V2_MAX_QUERIES,
    ScopeCursorQuantity,
    ScopeCursorReadoutDriverV2,
    ScopeCursorReadoutV2,
)
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.contracts import ScopeAnalysisReadDriver
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import ScopeCursorReadout
from wavebench.instruments.scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    SCOPE_STRICT_V2_CAPABILITIES,
    validate_scope_descriptor,
)
from wavebench.instruments.scope_extensions import (
    SCOPE_CURSOR_READOUT_V2_MAX_QUERIES as SCOPE_CURSOR_READOUT_V2_MAX_QUERIES_FROM_PROFILE,
    ScopeCursorReadoutProfileV2,
    ScopeDescriptorExtensions,
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


_READABLE_FIELDS = ("source_a", "source_b", "x_delta")
_UNAVAILABLE_FIELDS = (
    "x_a",
    "x_b",
    "inverse_x_delta",
    "y_a",
    "y_b",
    "y_delta",
)


def _profile(
    *,
    readable_fields: tuple[str, ...] = _READABLE_FIELDS,
    conditionally_applicable_fields: tuple[str, ...] = (),
    addressing: str = "global",
    max_queries: int = 1,
) -> ScopeCursorReadoutProfileV2:
    return ScopeCursorReadoutProfileV2(
        readable_fields=readable_fields,  # type: ignore[arg-type]
        conditionally_applicable_fields=conditionally_applicable_fields,  # type: ignore[arg-type]
        addressing=addressing,  # type: ignore[arg-type]
        max_queries=max_queries,
    )


def _quantity(value: float = 0.001, unit: str = "s") -> ScopeCursorQuantity:
    return ScopeCursorQuantity(value=value, unit=unit)  # type: ignore[arg-type]


def _global_result() -> ScopeCursorReadoutV2:
    return ScopeCursorReadoutV2(
        cursor_index=None,
        mode="MAN",
        function="TIME",
        source_a="CH1",
        source_b="CH2",
        x_delta=_quantity(),
        unavailable_fields=_UNAVAILABLE_FIELDS,
        not_applicable_fields=("cursor_index",),
    )


def _indexed_result(cursor_index: int = 1) -> ScopeCursorReadoutV2:
    return ScopeCursorReadoutV2(
        cursor_index=cursor_index,
        mode="MAN",
        function="TIME",
        source_a="CH1",
        source_b="CH1",
        x_delta=_quantity(),
        unavailable_fields=_UNAVAILABLE_FIELDS,
    )


def _conditional_global_result() -> ScopeCursorReadoutV2:
    return ScopeCursorReadoutV2(
        cursor_index=None,
        mode="MEAS",
        function="VMAX",
        source_a=None,
        source_b=None,
        x_delta=_quantity(),
        unavailable_fields=_UNAVAILABLE_FIELDS,
        not_applicable_fields=("cursor_index", "source_a", "source_b"),
    )


def _expanded_global_result() -> ScopeCursorReadoutV2:
    return ScopeCursorReadoutV2(
        cursor_index=None,
        mode="MAN",
        function="TIME",
        source_a="CH1",
        source_b="CH2",
        x_delta=_quantity(),
        y_delta=_quantity(2.0, "percent"),
        unavailable_fields=(
            "x_a",
            "x_b",
            "inverse_x_delta",
            "y_a",
            "y_b",
        ),
        not_applicable_fields=("cursor_index",),
    )


def _descriptor(
    *,
    profile: ScopeCursorReadoutProfileV2 | None = None,
    minimum: str = "0.8.24",
    capabilities: tuple[str, ...] = (),
    extensions: bool = True,
) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        driver_id="example.cursor-readout-v2",
        kind="scope",
        display_name="Example scope",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities or ("scope.cursor_readout_v2",),
        idn_patterns=("EXAMPLE,EX1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda _context: object(),
        wavebench_min_version=minimum,
        scope_extensions=(
            ScopeDescriptorExtensions(cursor_readout_profile_v2=profile or _profile())
            if extensions
            else None
        ),
    )


def test_cursor_readout_v2_models_serialize_global_and_indexed_readouts() -> None:
    global_result = _global_result()
    indexed_result = _indexed_result(2)
    source_quantity = ScopeCursorQuantity(2.0, "source", source_unit="V")

    assert asdict(global_result)["x_delta"] == {
        "value": 0.001,
        "unit": "s",
        "source_unit": None,
    }
    assert global_result.not_applicable_fields == ("cursor_index",)
    assert indexed_result.cursor_index == 2
    assert indexed_result.unavailable_fields == _UNAVAILABLE_FIELDS
    assert source_quantity.source_unit == "V"
    assert SCOPE_CURSOR_READOUT_V2_FIELD_ORDER == (
        "cursor_index",
        "source_a",
        "source_b",
        "x_a",
        "x_b",
        "x_delta",
        "inverse_x_delta",
        "y_a",
        "y_b",
        "y_delta",
    )


@pytest.mark.parametrize(
    ("unit", "source_unit"),
    (
        ("s", None),
        ("Hz", None),
        ("degree", None),
        ("percent", None),
        ("source", "V"),
    ),
)
def test_cursor_quantity_accepts_every_contract_unit(
    unit: str,
    source_unit: str | None,
) -> None:
    quantity = ScopeCursorQuantity(1.0, unit, source_unit=source_unit)  # type: ignore[arg-type]

    assert quantity.unit == unit
    assert quantity.source_unit == source_unit


@pytest.mark.parametrize(
    ("factory", "error_type", "message"),
    (
        (
            lambda: ScopeCursorQuantity(float("nan"), "s"),
            ValueError,
            "finite",
        ),
        (
            lambda: ScopeCursorQuantity(1.0, "unknown"),
            ValueError,
            "unsupported",
        ),
        (
            lambda: ScopeCursorQuantity(1.0, "s", source_unit="V"),
            ValueError,
            "requires",
        ),
        (
            lambda: ScopeCursorQuantity(1.0, "source", source_unit="not safe"),
            ValueError,
            "visible unit",
        ),
        (
            lambda: ScopeCursorQuantity(
                1.0,
                "source",
                source_unit="TCPIP::host::INSTR",
            ),
            ValueError,
            "visible unit",
        ),
        (
            lambda: ScopeCursorReadoutV2(
                True,
                "MAN",
                "TIME",
                "CH1",
                "CH1",
            ),
            ValueError,
            "positive integer",
        ),
        (
            lambda: ScopeCursorReadoutV2(
                None,
                "MAN",
                "TIME",
                "CH1",
                None,
            ),
            ValueError,
            "together",
        ),
        (
            lambda: ScopeCursorReadoutV2(
                None,
                "MAN",
                "TIME",
                "CH1",
                "CH1",
                x_delta=object(),  # type: ignore[arg-type]
            ),
            TypeError,
            "ScopeCursorQuantity",
        ),
        (
            lambda: ScopeCursorReadoutV2(
                None,
                "MAN",
                "TIME",
                "CH1",
                "CH1",
                x_delta=_quantity(),
                unavailable_fields=_UNAVAILABLE_FIELDS,
                not_applicable_fields=("cursor_index", "x_delta"),
            ),
            ValueError,
            "exactly describe",
        ),
        (
            lambda: ScopeCursorReadoutV2(
                None,
                "MAN",
                "TIME",
                None,
                None,
                x_delta=_quantity(),
                unavailable_fields=(
                    "source_a",
                    "x_a",
                    "x_b",
                    "inverse_x_delta",
                    "y_a",
                    "y_b",
                    "y_delta",
                ),
                not_applicable_fields=("cursor_index", "source_b"),
            ),
            ValueError,
            "same availability",
        ),
        (
            lambda: ScopeCursorReadoutV2(
                None,
                "MAN",
                "TIME",
                "CH1",
                "CH1",
                x_delta=_quantity(),
                unavailable_fields=tuple(reversed(_UNAVAILABLE_FIELDS)),
                not_applicable_fields=("cursor_index",),
            ),
            ValueError,
            "stable field order",
        ),
    ),
)
def test_cursor_readout_v2_models_reject_invalid_or_ambiguous_values(
    factory,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        factory()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"readable_fields": ()}, "must not be empty"),
        ({"readable_fields": ("source_a", "x_delta")}, "together"),
        ({"readable_fields": ("source_a", "source_b")}, "include a quantity"),
        ({"readable_fields": ("x_delta", "source_a", "source_b")}, "stable field order"),
        ({"conditionally_applicable_fields": ("y_delta",)}, "must be readable"),
        ({"readable_fields": ("cursor_index", "source_a", "source_b", "x_delta")}, "global"),
        ({"addressing": "indexed"}, "requires a non-conditional cursor_index"),
        (
            {
                "addressing": "indexed",
                "readable_fields": ("cursor_index", "source_a", "source_b", "x_delta"),
                "conditionally_applicable_fields": ("cursor_index",),
            },
            "non-conditional",
        ),
        ({"max_queries": True}, "integer"),
        ({"max_queries": 33}, "1..32"),
    ),
)
def test_cursor_readout_v2_profile_rejects_invalid_static_contract(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "readable_fields": _READABLE_FIELDS,
        "conditionally_applicable_fields": (),
        "addressing": "global",
        "max_queries": 1,
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError), match=message):
        ScopeCursorReadoutProfileV2(**values)  # type: ignore[arg-type]


def test_cursor_readout_v2_profile_enforces_global_index_and_conditional_paths() -> None:
    global_profile = _profile()
    global_profile.validate_request(cursor_index=None, configured_cursor=True)
    global_profile.validate_result(_global_result(), cursor_index=None)
    with pytest.raises(ValueError, match="global cursor addressing"):
        global_profile.validate_request(cursor_index=1, configured_cursor=True)
    with pytest.raises(ValueError, match="global cursor readout"):
        global_profile.validate_result(_indexed_result(), cursor_index=None)

    conditional_profile = _profile(
        conditionally_applicable_fields=("source_a", "source_b"),
    )
    conditional_profile.validate_result(_conditional_global_result(), cursor_index=None)
    with pytest.raises(ValueError, match="outside the descriptor profile"):
        conditional_profile.validate_result(_expanded_global_result(), cursor_index=None)

    indexed_profile = _profile(
        readable_fields=("cursor_index", "source_a", "source_b", "x_delta"),
        addressing="indexed",
    )
    indexed_profile.validate_request(cursor_index=1, configured_cursor=True)
    indexed_profile.validate_result(_indexed_result(), cursor_index=1)
    with pytest.raises(ValueError, match="wrong cursor_index"):
        indexed_profile.validate_result(_indexed_result(2), cursor_index=1)


def test_cursor_readout_v2_is_additive_and_registered_without_legacy_membership() -> None:
    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_cursor_readout_v2(
            self,
            cursor_index: int | None,
            *,
            configured_cursor: bool,
        ) -> ScopeCursorReadoutV2:
            assert cursor_index is None
            assert configured_cursor is True
            return _global_result()

    assert isinstance(Driver(), ScopeCursorReadoutDriverV2)
    assert "get_cursor_readout_v2" not in ScopeAnalysisReadDriver.__dict__
    assert SCOPE_CAPABILITY_METHODS["scope.cursor_readout_v2"] == (
        "get_cursor_readout_v2",
    )
    assert CAPABILITY_METHODS["scope.cursor_readout_v2"] == ("get_cursor_readout_v2",)
    assert "scope.cursor_readout_v2" in SCOPE_STRICT_V2_CAPABILITIES
    assert "scope.cursor_readout_v2" not in SCOPE_OPERATION_SPECS
    spec = SCOPE_PORTABILITY_V2_OPERATION_SPECS["scope.cursor_readout_v2"]
    assert require_operation_spec("scope.cursor_readout_v2") is spec
    assert spec.effect == "stateful_read"
    assert spec.lease_mode == "exclusive"
    assert spec.required_verified_fields == ()
    assert spec.error_check_minimum == "disabled"
    assert SCOPE_CURSOR_READOUT_V2_MAX_QUERIES == 32
    assert SCOPE_CURSOR_READOUT_V2_MAX_QUERIES_FROM_PROFILE == 32


def test_cursor_readout_v2_descriptor_requires_profile_core_floor_and_callable_method() -> None:
    with pytest.raises(ConfigError, match="scope portability V2 capabilities require.*0.8.24"):
        validate_scope_descriptor(_descriptor(minimum="0.8.23"))
    with pytest.raises(ConfigError, match="cursor_readout_profile_v2"):
        validate_scope_descriptor(_descriptor(extensions=False))

    class MissingMethod:
        def close(self) -> None:
            pass

    with pytest.raises(TypeError, match="get_cursor_readout_v2"):
        validate_declared_capabilities(_descriptor(), MissingMethod())


def test_cursor_readout_v2_method_does_not_create_an_implicit_capability() -> None:
    descriptor = _descriptor(capabilities=("scope.idn",), extensions=False)

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_cursor_readout_v2(
            self,
            cursor_index: int | None,
            *,
            configured_cursor: bool,
        ) -> ScopeCursorReadoutV2:
            raise AssertionError(
                "undeclared cursor V2 must not be called "
                f"for {cursor_index}/{configured_cursor}"
            )

    validate_declared_capabilities(descriptor, Driver())
    assert "scope.cursor_readout_v2" not in descriptor.capabilities


def _service(
    *,
    profile: ScopeCursorReadoutProfileV2,
    driver: object,
    transport: GuardedAuditedTransport | None = None,
) -> ScopeService:
    return ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.cursor-readout-v2"),
            connection=SimpleNamespace(timeout_ms=1_000),
        ),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.cursor-readout-v2",
            capabilities=("scope.cursor_readout_v2",),
            scope_extensions=ScopeDescriptorExtensions(cursor_readout_profile_v2=profile),
        ),
        transport=transport,
        session_state=None if transport is None else transport.session_state,
    )


def test_cursor_readout_v2_service_only_calls_v2_and_keeps_legacy_route_separate() -> None:
    calls: list[tuple[str, int | None]] = []
    driver = SimpleNamespace(
        get_cursor_readout_v2=lambda cursor_index, *, configured_cursor: calls.append(
            ("v2", cursor_index)
        )
        or _global_result(),
        get_cursor_readout=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy cursor must stay separate")
        ),
    )
    service = _service(profile=_profile(), driver=driver)

    assert service.cursor_readout_v2(None, configured_cursor=True) == _global_result()
    assert calls == [("v2", None)]

    legacy = ScopeCursorReadout(cursor_index=1, source="CH1", function="TIME")
    driver.get_cursor_readout = (
        lambda index, *, configured_cursor: calls.append(("legacy", index)) or legacy
    )
    service.descriptor = SimpleNamespace(
        driver_id="example.cursor-readout-v2",
        capabilities=("scope.cursor_readout", "scope.cursor_readout_v2"),
        scope_extensions=ScopeDescriptorExtensions(cursor_readout_profile_v2=_profile()),
    )
    assert service.cursor_readout(1, configured_cursor=True) is legacy
    assert calls == [("v2", None), ("legacy", 1)]


@pytest.mark.parametrize(
    ("cursor_index", "configured_cursor", "message"),
    (
        (True, True, "positive integer"),
        (0, True, "positive integer"),
        ("1", True, "positive integer"),
        (None, False, "configured_cursor=True"),
        (None, 1, "configured_cursor=True"),
        (1, True, "global cursor addressing"),
    ),
)
def test_cursor_readout_v2_service_rejects_invalid_preconditions_before_opening_scope(
    cursor_index: object,
    configured_cursor: object,
    message: str,
) -> None:
    service = _service(
        profile=_profile(),
        driver=SimpleNamespace(get_cursor_readout_v2=lambda *_args, **_kwargs: _global_result()),
    )
    service._open_scope = lambda: pytest.fail("invalid cursor V2 request must not open scope")

    with pytest.raises(ConfigError, match=message):
        service.cursor_readout_v2(  # type: ignore[arg-type]
            cursor_index,
            configured_cursor=configured_cursor,
        )


def test_cursor_readout_v2_service_rejects_invalid_result_without_fallback() -> None:
    service = _service(
        profile=_profile(),
        driver=SimpleNamespace(get_cursor_readout_v2=lambda *_args, **_kwargs: object()),
    )

    with pytest.raises(DataError, match="invalid result"):
        service.cursor_readout_v2(None, configured_cursor=True)


def test_cursor_readout_v2_second_fixture_exercises_indexed_hz_and_source_units() -> None:
    profile = _profile(
        readable_fields=(
            "cursor_index",
            "source_a",
            "source_b",
            "x_delta",
            "y_delta",
        ),
        addressing="indexed",
    )
    result = ScopeCursorReadoutV2(
        cursor_index=2,
        mode="TRAC",
        function="TIME",
        source_a="CH1",
        source_b="MATH1",
        x_delta=ScopeCursorQuantity(1_000.0, "Hz"),
        y_delta=ScopeCursorQuantity(2.0, "source", source_unit="V"),
        unavailable_fields=("x_a", "x_b", "inverse_x_delta", "y_a", "y_b"),
    )

    service = _service(
        profile=profile,
        driver=SimpleNamespace(
            get_cursor_readout_v2=lambda cursor_index, *, configured_cursor: result
        ),
    )

    assert service.cursor_readout_v2(2, configured_cursor=True) is result


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
        return "MAN"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def close(self) -> None:
        pass


def test_cursor_readout_v2_uses_only_budgeted_text_queries_without_legacy_preflight() -> None:
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)

    class Driver:
        def idn(self) -> str:
            raise AssertionError("cursor V2 must not call legacy identity preflight")

        def get_cursor_readout(self, *_args, **_kwargs) -> ScopeCursorReadout:
            raise AssertionError("cursor V2 must not call legacy cursor")

        def get_cursor_readout_v2(
            self,
            cursor_index: int | None,
            *,
            configured_cursor: bool,
        ) -> ScopeCursorReadoutV2:
            assert cursor_index is None
            assert configured_cursor is True
            transport.query(":CURS:MODE?")
            return _global_result()

    service = _service(profile=_profile(max_queries=1), driver=Driver(), transport=transport)

    assert service.cursor_readout_v2(None, configured_cursor=True) == _global_result()
    assert inner.queries == [":CURS:MODE?"]
    assert inner.writes == []
    assert transport.counters.query_calls == 1


@pytest.mark.parametrize("mode", ("overrun", "write"))
def test_cursor_readout_v2_rejects_overrun_and_non_query_io_before_backend_send(
    mode: str,
) -> None:
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)

    class Driver:
        def get_cursor_readout_v2(
            self,
            cursor_index: int | None,
            *,
            configured_cursor: bool,
        ) -> ScopeCursorReadoutV2:
            assert cursor_index is None
            assert configured_cursor is True
            transport.query(":CURS:MODE?")
            if mode == "overrun":
                transport.query(":CURS:FUNC?")
            else:
                transport.write(":CURS:BAD")
            return _global_result()

    service = _service(profile=_profile(max_queries=1), driver=Driver(), transport=transport)

    with pytest.raises(SessionHealthError):
        service.cursor_readout_v2(None, configured_cursor=True)
    assert inner.queries == [":CURS:MODE?"]
    assert inner.writes == []


def test_cursor_readout_v2_rejects_guarded_transport_without_shared_session_state() -> None:
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)
    service = _service(
        profile=_profile(),
        transport=transport,
        driver=SimpleNamespace(
            get_cursor_readout_v2=lambda *_args, **_kwargs: transport.write(":CURS:BAD")
        ),
    )
    object.__setattr__(service, "session_state", None)

    with pytest.raises(ConfigError, match="shared instrument session state"):
        service.cursor_readout_v2(None, configured_cursor=True)
    assert inner.queries == []
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
        driver_reference="example.cursor-readout-v2",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )


def test_cursor_readout_v2_factory_latch_blocks_construction_io_until_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()
    errors: list[TransportIOError] = []

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_cursor_readout_v2(
            self,
            cursor_index: int | None,
            *,
            configured_cursor: bool,
        ) -> ScopeCursorReadoutV2:
            assert cursor_index is None
            assert configured_cursor is True
            return _global_result()

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


def test_cursor_readout_v2_factory_closes_missing_method_without_io(
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

    with pytest.raises(ConfigError, match="get_cursor_readout_v2"):
        _open_factory_descriptor()

    assert inner.queries == []
    assert inner.closed == 1


def test_undeclared_cursor_readout_v2_method_does_not_latch_legacy_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()

    class LegacyDriver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_cursor_readout_v2(
            self,
            cursor_index: int | None,
            *,
            configured_cursor: bool,
        ) -> ScopeCursorReadoutV2:
            raise AssertionError(
                "undeclared cursor V2 must not be called "
                f"for {cursor_index}/{configured_cursor}"
            )

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
