from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from wavebench.errors import ConfigError, TransportIOError
from wavebench.instruments import (
    InstrumentDescriptor,
    ScopeChannelInputStateDriverV2,
    ScopeChannelInputStateV2,
)
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.contracts import ScopeDriver
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    SCOPE_STRICT_V2_CAPABILITIES,
    validate_scope_descriptor,
)
from wavebench.services.operation_specs import require_operation_spec
from wavebench.logging import CommandLogger
from wavebench.transport.contracts import ReplayPolicy


def _descriptor(*, minimum: str = "0.8.24", capabilities: tuple[str, ...] = ()) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        driver_id="example.input-state",
        kind="scope",
        display_name="Example Scope",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities or ("scope.channel_input_state_v2",),
        idn_patterns=("EXAMPLE,EX1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda _context: object(),
        wavebench_min_version=minimum,
    )


def test_input_state_v2_serializes_available_and_unavailable_impedance() -> None:
    available = ScopeChannelInputStateV2(
        channel=1,
        coupling="dc",
        termination="high_z",
        impedance_ohm=1_000_000.0,
    )
    unavailable = ScopeChannelInputStateV2(
        channel=2,
        coupling="unknown",
        termination="unknown",
        impedance_ohm=None,
        unavailable_fields=("impedance_ohm",),
    )

    assert asdict(available) == {
        "channel": 1,
        "coupling": "dc",
        "termination": "high_z",
        "impedance_ohm": 1_000_000.0,
        "unavailable_fields": (),
    }
    assert asdict(unavailable) == {
        "channel": 2,
        "coupling": "unknown",
        "termination": "unknown",
        "impedance_ohm": None,
        "unavailable_fields": ("impedance_ohm",),
    }


@pytest.mark.parametrize(
    ("changes", "error_type", "message"),
    (
        ({"channel": True}, ValueError, "positive"),
        ({"channel": 0}, ValueError, "positive"),
        ({"coupling": "DC"}, ValueError, "coupling"),
        ({"termination": "75_ohm"}, ValueError, "termination"),
        ({"impedance_ohm": False}, ValueError, "finite positive"),
        ({"impedance_ohm": 0.0}, ValueError, "finite positive"),
        ({"impedance_ohm": float("nan")}, ValueError, "finite positive"),
        ({"impedance_ohm": None, "unavailable_fields": ()}, ValueError, "marked unavailable"),
        ({"impedance_ohm": None, "unavailable_fields": ("other",)}, ValueError, "marked unavailable"),
        ({"unavailable_fields": ["impedance_ohm"]}, TypeError, "must be a tuple"),
        ({"unavailable_fields": ("impedance_ohm",)}, ValueError, "available impedance"),
    ),
)
def test_input_state_v2_rejects_invalid_or_ambiguous_values(
    changes: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    values: dict[str, object] = {
        "channel": 1,
        "coupling": "ac",
        "termination": "50_ohm",
        "impedance_ohm": 50.0,
        "unavailable_fields": (),
    }
    values.update(changes)

    with pytest.raises(error_type, match=message):
        ScopeChannelInputStateV2(**values)  # type: ignore[arg-type]


def test_input_state_v2_is_an_additive_protocol_and_registered_capability() -> None:
    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

        def get_channel_input_state_v2(self, channel: int) -> ScopeChannelInputStateV2:
            return ScopeChannelInputStateV2(
                channel=channel,
                coupling="dc",
                termination="high_z",
                impedance_ohm=1_000_000.0,
            )

    assert isinstance(Driver(), ScopeChannelInputStateDriverV2)
    assert "get_channel_input_state_v2" not in ScopeDriver.__dict__
    assert SCOPE_CAPABILITY_METHODS["scope.channel_input_state_v2"] == (
        "get_channel_input_state_v2",
    )
    assert CAPABILITY_METHODS["scope.channel_input_state_v2"] == (
        "get_channel_input_state_v2",
    )
    assert SCOPE_STRICT_V2_CAPABILITIES == {"scope.channel_input_state_v2"}


def test_input_state_v2_descriptor_requires_its_own_core_floor_and_method() -> None:
    descriptor = _descriptor(minimum="0.8.23")
    with pytest.raises(ConfigError, match="scope portability V2 capabilities require.*0.8.24"):
        validate_scope_descriptor(descriptor)

    class MissingMethod:
        def close(self) -> None:
            pass

    with pytest.raises(TypeError, match="get_channel_input_state_v2"):
        validate_declared_capabilities(_descriptor(), MissingMethod())


def test_input_state_v2_method_does_not_create_an_implicit_capability() -> None:
    descriptor = _descriptor(capabilities=("scope.idn",))

    class Driver:
        def close(self) -> None:
            pass

        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def get_channel_input_state_v2(self, channel: int) -> ScopeChannelInputStateV2:
            raise AssertionError("unrequested capability must not be used")

    validate_declared_capabilities(descriptor, Driver())
    assert "scope.channel_input_state_v2" not in descriptor.capabilities


def test_input_state_v2_operation_is_a_stateful_exclusive_read() -> None:
    spec = require_operation_spec("scope.channel_input_state_v2")

    assert spec.instrument_kind == "scope"
    assert spec.required_capabilities == ("scope.channel_input_state_v2",)
    assert spec.effect == "stateful_read"
    assert spec.lease_mode == "exclusive"


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
        driver_reference="example.input-state",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )


def test_input_state_v2_factory_latch_blocks_io_until_capability_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()
    errors: list[TransportIOError] = []

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

        def get_channel_input_state_v2(self, channel: int) -> ScopeChannelInputStateV2:
            return ScopeChannelInputStateV2(
                channel=channel,
                coupling="dc",
                termination="high_z",
                impedance_ohm=1_000_000.0,
            )

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

    assert len(errors) == 1
    assert errors[0].reason_code == "factory_construction_pending"
    assert errors[0].attempts == 0
    assert inner.queries == []
    assert opened.transport is not None
    assert opened.transport.query("*IDN?") == "ok"
    assert inner.queries == ["*IDN?"]


def test_input_state_v2_factory_closes_on_missing_method_without_io(
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

    with pytest.raises(ConfigError, match="get_channel_input_state_v2"):
        _open_factory_descriptor()

    assert inner.queries == []
    assert inner.closed == 1


def test_extra_input_state_method_does_not_latch_legacy_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()

    class LegacyDriver:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

        def get_channel_input_state_v2(self, channel: int) -> ScopeChannelInputStateV2:
            raise AssertionError("undeclared method must not be used")

    descriptor = replace(
        _descriptor(capabilities=("scope.idn",)),
        factory=lambda context: (context.open_transport().query("*IDN?"), LegacyDriver())[1],
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda _reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **_kwargs: inner)

    _open_factory_descriptor()

    assert inner.queries == ["*IDN?"]
