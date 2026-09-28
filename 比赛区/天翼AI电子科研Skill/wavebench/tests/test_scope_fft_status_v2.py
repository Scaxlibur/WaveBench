from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from wavebench.errors import ConfigError, DataError, SessionHealthError, TransportIOError
from wavebench.instruments import (
    InstrumentDescriptor,
    SCOPE_FFT_STATUS_V2_FIELD_ORDER,
    ScopeFftStatusDriverV2,
    ScopeFftStatusV2,
)
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.contracts import ScopeAnalysisReadDriver
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import ScopeFftStatus
from wavebench.instruments.scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    SCOPE_STRICT_V2_CAPABILITIES,
    validate_scope_descriptor,
)
from wavebench.instruments.scope_extensions import (
    ScopeDescriptorExtensions,
    ScopeFftStatusProfileV2,
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


_READABLE_FIELDS = (
    "source",
    "window",
    "vertical_unit",
    "frequency_start_hz",
    "frequency_stop_hz",
)
_UNAVAILABLE_FIELDS = (
    "average_complete",
    "resolution_bandwidth_hz",
    "sample_rate_hz",
)


def _profile(
    *,
    readable_fields: tuple[str, ...] = _READABLE_FIELDS,
    max_queries: int = 1,
) -> ScopeFftStatusProfileV2:
    return ScopeFftStatusProfileV2(
        readable_fields=readable_fields,  # type: ignore[arg-type]
        max_queries=max_queries,
    )


def _result(math_index: int = 1) -> ScopeFftStatusV2:
    return ScopeFftStatusV2(
        math_index=math_index,
        source="CH1",
        window="HANNING",
        vertical_unit="DBV",
        frequency_start_hz=0.0,
        frequency_stop_hz=1_000_000.0,
        unavailable_fields=_UNAVAILABLE_FIELDS,
    )


def _complete_result(math_index: int = 1) -> ScopeFftStatusV2:
    return ScopeFftStatusV2(
        math_index=math_index,
        source="CH1",
        window="HANNING",
        vertical_unit="DBV",
        frequency_start_hz=0.0,
        frequency_stop_hz=1_000_000.0,
        average_complete=True,
        resolution_bandwidth_hz=100.0,
        sample_rate_hz=2_000_000.0,
    )


def _descriptor(
    *,
    profile: ScopeFftStatusProfileV2 | None = None,
    minimum: str = "0.8.24",
    capabilities: tuple[str, ...] = (),
    extensions: bool = True,
) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        driver_id="example.fft-status-v2",
        kind="scope",
        display_name="Example scope",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities or ("scope.fft_status_v2",),
        idn_patterns=("EXAMPLE,EX1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda _context: object(),
        wavebench_min_version=minimum,
        scope_extensions=(
            ScopeDescriptorExtensions(fft_status_profile_v2=profile or _profile())
            if extensions
            else None
        ),
    )


def test_fft_status_v2_models_serialize_partial_and_complete_readouts() -> None:
    partial = _result()
    complete = _complete_result(2)

    assert asdict(partial)["source"] == "CH1"
    assert partial.unavailable_fields == _UNAVAILABLE_FIELDS
    assert complete.unavailable_fields == ()
    assert complete.resolution_bandwidth_hz == 100.0
    assert SCOPE_FFT_STATUS_V2_FIELD_ORDER == (
        "source",
        "window",
        "vertical_unit",
        "frequency_start_hz",
        "frequency_stop_hz",
        "average_complete",
        "resolution_bandwidth_hz",
        "sample_rate_hz",
    )


@pytest.mark.parametrize(
    ("factory", "error_type", "message"),
    (
        (lambda: ScopeFftStatusV2(True, unavailable_fields=SCOPE_FFT_STATUS_V2_FIELD_ORDER), ValueError, "positive integer"),
        (
            lambda: ScopeFftStatusV2(
                1,
                source="not safe",
                unavailable_fields=(
                    "window",
                    "vertical_unit",
                    "frequency_start_hz",
                    "frequency_stop_hz",
                    "average_complete",
                    "resolution_bandwidth_hz",
                    "sample_rate_hz",
                ),
            ),
            ValueError,
            "safe token",
        ),
        (
            lambda: ScopeFftStatusV2(
                1,
                frequency_start_hz=0.0,
                unavailable_fields=(
                    "source",
                    "window",
                    "vertical_unit",
                    "frequency_stop_hz",
                    "average_complete",
                    "resolution_bandwidth_hz",
                    "sample_rate_hz",
                ),
            ),
            ValueError,
            "present or unavailable together",
        ),
        (
            lambda: ScopeFftStatusV2(
                1,
                frequency_start_hz=2.0,
                frequency_stop_hz=1.0,
                unavailable_fields=(
                    "source",
                    "window",
                    "vertical_unit",
                    "average_complete",
                    "resolution_bandwidth_hz",
                    "sample_rate_hz",
                ),
            ),
            ValueError,
            "below",
        ),
        (
            lambda: ScopeFftStatusV2(
                1,
                resolution_bandwidth_hz=0.0,
                unavailable_fields=(
                    "source",
                    "window",
                    "vertical_unit",
                    "frequency_start_hz",
                    "frequency_stop_hz",
                    "average_complete",
                    "sample_rate_hz",
                ),
            ),
            ValueError,
            "positive",
        ),
        (
            lambda: ScopeFftStatusV2(
                1,
                unavailable_fields=("source", "source", "window", "vertical_unit", "frequency_start_hz", "frequency_stop_hz", "average_complete", "resolution_bandwidth_hz", "sample_rate_hz"),
            ),
            ValueError,
            "duplicates",
        ),
        (
            lambda: ScopeFftStatusV2(
                1,
                unavailable_fields=tuple(reversed(SCOPE_FFT_STATUS_V2_FIELD_ORDER)),
            ),
            ValueError,
            "stable order",
        ),
    ),
)
def test_fft_status_v2_models_reject_invalid_or_ambiguous_values(
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
        ({"readable_fields": tuple(reversed(_READABLE_FIELDS))}, "stable field order"),
        ({"readable_fields": ("source", "unknown")}, "unsupported"),
        ({"readable_fields": ("frequency_start_hz",)}, "together"),
        ({"max_queries": True}, "integer"),
        ({"max_queries": 33}, "1..32"),
    ),
)
def test_fft_status_v2_profile_rejects_invalid_static_contract(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "readable_fields": _READABLE_FIELDS,
        "max_queries": 1,
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError), match=message):
        ScopeFftStatusProfileV2(**values)  # type: ignore[arg-type]


def test_fft_status_v2_profile_enforces_static_result_boundary() -> None:
    profile = _profile()

    profile.validate_result(_result(), math_index=1)
    with pytest.raises(ValueError, match="outside the descriptor profile"):
        profile.validate_result(_complete_result(), math_index=1)
    with pytest.raises(ValueError, match="wrong math_index"):
        profile.validate_result(_result(2), math_index=1)


def test_fft_status_v2_is_additive_and_registered_without_r13_membership() -> None:
    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_fft_status_v2(
            self,
            math_index: int,
            *,
            configured_fft: bool,
        ) -> ScopeFftStatusV2:
            assert configured_fft is True
            return _result(math_index)

    assert isinstance(Driver(), ScopeFftStatusDriverV2)
    assert "get_fft_status_v2" not in ScopeAnalysisReadDriver.__dict__
    assert SCOPE_CAPABILITY_METHODS["scope.fft_status_v2"] == ("get_fft_status_v2",)
    assert CAPABILITY_METHODS["scope.fft_status_v2"] == ("get_fft_status_v2",)
    assert "scope.fft_status_v2" in SCOPE_STRICT_V2_CAPABILITIES
    assert "scope.fft_status_v2" not in SCOPE_OPERATION_SPECS
    spec = SCOPE_PORTABILITY_V2_OPERATION_SPECS["scope.fft_status_v2"]
    assert require_operation_spec("scope.fft_status_v2") is spec
    assert spec.effect == "stateful_read"
    assert spec.lease_mode == "exclusive"
    assert spec.required_verified_fields == ()
    assert spec.error_check_minimum == "disabled"


def test_fft_status_v2_descriptor_requires_profile_core_floor_and_callable_method() -> None:
    with pytest.raises(ConfigError, match="scope portability V2 capabilities require.*0.8.24"):
        validate_scope_descriptor(_descriptor(minimum="0.8.23"))
    with pytest.raises(ConfigError, match="fft_status_profile_v2"):
        validate_scope_descriptor(_descriptor(extensions=False))

    class MissingMethod:
        def close(self) -> None:
            pass

    with pytest.raises(TypeError, match="get_fft_status_v2"):
        validate_declared_capabilities(_descriptor(), MissingMethod())


def test_fft_status_v2_method_does_not_create_an_implicit_capability() -> None:
    descriptor = _descriptor(capabilities=("scope.idn",), extensions=False)

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_fft_status_v2(
            self,
            math_index: int,
            *,
            configured_fft: bool,
        ) -> ScopeFftStatusV2:
            raise AssertionError(
                f"undeclared FFT V2 must not be called for {math_index}/{configured_fft}"
            )

    validate_declared_capabilities(descriptor, Driver())
    assert "scope.fft_status_v2" not in descriptor.capabilities


def _service(
    *,
    profile: ScopeFftStatusProfileV2,
    driver: object,
    transport: GuardedAuditedTransport | None = None,
) -> ScopeService:
    return ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.fft-status-v2"),
            connection=SimpleNamespace(timeout_ms=1_000),
        ),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.fft-status-v2",
            capabilities=("scope.fft_status_v2",),
            scope_extensions=ScopeDescriptorExtensions(fft_status_profile_v2=profile),
        ),
        transport=transport,
        session_state=None if transport is None else transport.session_state,
    )


def test_fft_status_v2_service_only_calls_v2_and_keeps_legacy_route_separate() -> None:
    calls: list[tuple[str, int]] = []
    driver = SimpleNamespace(
        get_fft_status_v2=lambda math_index, *, configured_fft: calls.append(("v2", math_index))
        or _result(math_index),
        get_fft_status=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy FFT must stay separate")
        ),
        get_math_waveform_metadata=lambda _index: (_ for _ in ()).throw(
            AssertionError("math metadata must not be used for FFT V2")
        ),
    )
    service = _service(profile=_profile(), driver=driver)

    assert service.fft_status_v2(1, configured_fft=True) == _result()
    assert calls == [("v2", 1)]

    legacy = ScopeFftStatus(
        math_index=1,
        average_complete=True,
        resolution_bandwidth_hz=100.0,
        sample_rate_hz=2_000_000.0,
    )
    driver.get_fft_status = lambda index, *, configured_fft: calls.append(("legacy", index)) or legacy
    service.descriptor = SimpleNamespace(
        driver_id="example.fft-status-v2",
        capabilities=("scope.fft_status", "scope.fft_status_v2"),
        scope_extensions=ScopeDescriptorExtensions(fft_status_profile_v2=_profile()),
    )
    assert service.fft_status(1, configured_fft=True) is legacy
    assert calls == [("v2", 1), ("legacy", 1)]


@pytest.mark.parametrize(
    ("math_index", "configured_fft", "message"),
    (
        (0, True, "positive integer"),
        (True, True, "positive integer"),
        (1, False, "configured_fft=True"),
        (1, 1, "configured_fft=True"),
    ),
)
def test_fft_status_v2_service_rejects_invalid_preconditions_before_opening_scope(
    math_index: object,
    configured_fft: object,
    message: str,
) -> None:
    service = _service(
        profile=_profile(),
        driver=SimpleNamespace(get_fft_status_v2=lambda *_args, **_kwargs: _result()),
    )
    service._open_scope = lambda: pytest.fail("invalid FFT V2 request must not open scope")

    with pytest.raises(ConfigError, match=message):
        service.fft_status_v2(math_index, configured_fft=configured_fft)  # type: ignore[arg-type]


def test_fft_status_v2_service_rejects_invalid_result_without_fallback() -> None:
    service = _service(
        profile=_profile(),
        driver=SimpleNamespace(get_fft_status_v2=lambda *_args, **_kwargs: object()),
    )

    with pytest.raises(DataError, match="invalid result"):
        service.fft_status_v2(1, configured_fft=True)


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
        return "FFT"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def close(self) -> None:
        pass


def test_fft_status_v2_uses_only_budgeted_text_queries_without_identity_or_math_preflight() -> None:
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)

    class Driver:
        def idn(self) -> str:
            raise AssertionError("FFT V2 must not call legacy identity preflight")

        def get_math_waveform_metadata(self, _index: int) -> object:
            raise AssertionError("FFT V2 must not query math metadata")

        def get_fft_status_v2(
            self,
            math_index: int,
            *,
            configured_fft: bool,
        ) -> ScopeFftStatusV2:
            assert math_index == 1
            assert configured_fft is True
            transport.query("MATH1:TYPE?")
            return _result(math_index)

    service = _service(profile=_profile(max_queries=1), driver=Driver(), transport=transport)

    assert service.fft_status_v2(1, configured_fft=True) == _result()
    assert inner.queries == ["MATH1:TYPE?"]
    assert inner.writes == []
    assert transport.counters.query_calls == 1


@pytest.mark.parametrize("mode", ("overrun", "write"))
def test_fft_status_v2_rejects_overrun_and_non_query_io_before_backend_send(mode: str) -> None:
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)

    class Driver:
        def get_fft_status_v2(
            self,
            math_index: int,
            *,
            configured_fft: bool,
        ) -> ScopeFftStatusV2:
            assert configured_fft is True
            transport.query("MATH1:TYPE?")
            if mode == "overrun":
                transport.query("MATH1:EXTRA?")
            else:
                transport.write("MATH1:BAD")
            return _result(math_index)

    service = _service(profile=_profile(max_queries=1), driver=Driver(), transport=transport)

    with pytest.raises(SessionHealthError):
        service.fft_status_v2(1, configured_fft=True)
    assert inner.queries == ["MATH1:TYPE?"]
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
        driver_reference="example.fft-status-v2",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )


def test_fft_status_v2_factory_latch_blocks_construction_io_until_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()
    errors: list[TransportIOError] = []

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_fft_status_v2(
            self,
            math_index: int,
            *,
            configured_fft: bool,
        ) -> ScopeFftStatusV2:
            assert configured_fft is True
            return _result(math_index)

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


def test_fft_status_v2_factory_closes_missing_method_without_io(
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

    with pytest.raises(ConfigError, match="get_fft_status_v2"):
        _open_factory_descriptor()

    assert inner.queries == []
    assert inner.closed == 1


def test_undeclared_fft_status_v2_method_does_not_latch_legacy_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()

    class LegacyDriver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_fft_status_v2(
            self,
            math_index: int,
            *,
            configured_fft: bool,
        ) -> ScopeFftStatusV2:
            raise AssertionError(
                f"undeclared FFT V2 must not be called for {math_index}/{configured_fft}"
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
