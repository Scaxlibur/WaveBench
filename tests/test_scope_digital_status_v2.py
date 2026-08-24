from __future__ import annotations

from dataclasses import asdict, replace
import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wavebench.cli import main
from wavebench.errors import ConfigError, DataError, TransportIOError
from wavebench.instruments import (
    InstrumentDescriptor,
    ScopeDigitalChannelStatusV2,
    ScopeDigitalPodStatusV2,
    ScopeDigitalSharedStatusV2,
    ScopeDigitalStatusDriverV2,
)
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.contracts import ScopeDigitalStatusDriver
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import ScopeDigitalChannelStatus
from wavebench.instruments.scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    SCOPE_STRICT_V2_CAPABILITIES,
    validate_scope_descriptor,
)
from wavebench.logging import CommandLogger
from wavebench.services.capability_explain import explain_operation
from wavebench.services.operation_specs import require_operation_spec
from wavebench.services.scope_service import ScopeService
from wavebench.transport.contracts import ReplayPolicy


def _descriptor(
    *,
    minimum: str = "0.8.24",
    capabilities: tuple[str, ...] = (),
) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        driver_id="example.digital-status",
        kind="scope",
        display_name="Example MSO",
        manufacturer="Example",
        models=("MSO1",),
        aliases=(),
        capabilities=capabilities or ("scope.digital_status_v2",),
        idn_patterns=("EXAMPLE,MSO1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda _context: object(),
        wavebench_min_version=minimum,
    )


def _complete_status(channel: int = 3) -> ScopeDigitalChannelStatusV2:
    return ScopeDigitalChannelStatusV2(
        channel=channel,
        displayed=True,
        position_div=-1.5,
        label="DATA",
        label_enabled=False,
        activity="TOGGLE",
        technology="TTL",
        hysteresis="NORMAL",
        pod=ScopeDigitalPodStatusV2(
            start_channel=0,
            stop_channel=7,
            threshold_v=1.4,
            threshold_scope="pod",
        ),
        shared=ScopeDigitalSharedStatusV2(
            module_present=True,
            timing_calibration_s=0.0,
            size="SMALL",
        ),
    )


def test_digital_status_v2_serializes_complete_and_partial_scoped_state() -> None:
    complete = _complete_status()
    partial = ScopeDigitalChannelStatusV2(
        channel=0,
        displayed=False,
        label_enabled=True,
        activity="unknown",
        shared=ScopeDigitalSharedStatusV2(module_present=True),
        unavailable_fields=(
            "position_div",
            "label",
            "technology",
            "hysteresis",
            "pod",
            "shared.timing_calibration_s",
            "shared.size",
        ),
    )

    assert asdict(complete)["unavailable_fields"] == ()
    assert asdict(complete)["pod"] == {
        "start_channel": 0,
        "stop_channel": 7,
        "threshold_v": 1.4,
        "threshold_scope": "pod",
    }
    assert asdict(partial) == {
        "channel": 0,
        "displayed": False,
        "position_div": None,
        "label": None,
        "label_enabled": True,
        "activity": "unknown",
        "technology": None,
        "hysteresis": None,
        "pod": None,
        "shared": {
            "module_present": True,
            "timing_calibration_s": None,
            "size": None,
        },
        "unavailable_fields": (
            "position_div",
            "label",
            "technology",
            "hysteresis",
            "pod",
            "shared.timing_calibration_s",
            "shared.size",
        ),
    }


@pytest.mark.parametrize(
    ("factory", "error_type", "message"),
    (
        (
            lambda: ScopeDigitalPodStatusV2(3, 2),
            ValueError,
            "must not exceed",
        ),
        (
            lambda: ScopeDigitalPodStatusV2(True, 2),
            ValueError,
            "non-negative",
        ),
        (
            lambda: ScopeDigitalPodStatusV2(0, 1, threshold_v=float("nan")),
            ValueError,
            "finite",
        ),
        (
            lambda: ScopeDigitalSharedStatusV2(),
            ValueError,
            "at least one",
        ),
        (
            lambda: ScopeDigitalSharedStatusV2(module_present=1),
            ValueError,
            "must be bool",
        ),
        (
            lambda: ScopeDigitalSharedStatusV2(size="LARGEISH"),
            ValueError,
            "size",
        ),
    ),
)
def test_digital_status_v2_nested_models_reject_invalid_values(
    factory,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        factory()


@pytest.mark.parametrize(
    ("changes", "error_type", "message"),
    (
        ({"channel": True}, ValueError, "non-negative"),
        ({"channel": -1}, ValueError, "non-negative"),
        ({"displayed": 1}, ValueError, "displayed"),
        ({"position_div": float("inf")}, ValueError, "position_div"),
        ({"label": False}, ValueError, "label"),
        ({"activity": "PULSE"}, ValueError, "activity"),
        ({"pod": ScopeDigitalPodStatusV2(8, 9)}, ValueError, "include"),
        ({"shared": object()}, TypeError, "shared"),
        ({"unavailable_fields": ("pod.threshold_v",)}, ValueError, "exactly describe"),
        ({"unavailable_fields": ("pod", "pod")}, ValueError, "duplicates"),
        ({"unavailable_fields": ("missing",)}, ValueError, "unsupported"),
    ),
)
def test_digital_status_v2_rejects_invalid_or_ambiguous_availability(
    changes: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    complete = _complete_status()
    values = {
        "channel": complete.channel,
        "displayed": complete.displayed,
        "position_div": complete.position_div,
        "label": complete.label,
        "label_enabled": complete.label_enabled,
        "activity": complete.activity,
        "technology": complete.technology,
        "hysteresis": complete.hysteresis,
        "pod": complete.pod,
        "shared": complete.shared,
        "unavailable_fields": complete.unavailable_fields,
    }
    values.update(changes)
    with pytest.raises(error_type, match=message):
        ScopeDigitalChannelStatusV2(**values)  # type: ignore[arg-type]


def test_digital_status_v2_is_an_additive_protocol_and_registered_capability() -> None:
    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,MSO1"

        def close(self) -> None:
            pass

        def get_digital_status_v2(self, channel: int) -> ScopeDigitalChannelStatusV2:
            return _complete_status(channel)

    assert isinstance(Driver(), ScopeDigitalStatusDriverV2)
    assert "get_digital_status_v2" not in ScopeDigitalStatusDriver.__dict__
    assert SCOPE_CAPABILITY_METHODS["scope.digital_status_v2"] == (
        "get_digital_status_v2",
    )
    assert CAPABILITY_METHODS["scope.digital_status_v2"] == (
        "get_digital_status_v2",
    )
    assert {
        "scope.channel_input_state_v2",
        "scope.digital_status_v2",
    } <= SCOPE_STRICT_V2_CAPABILITIES


def test_digital_status_v2_descriptor_requires_core_floor_and_callable_method() -> None:
    with pytest.raises(ConfigError, match="scope portability V2 capabilities require.*0.8.24"):
        validate_scope_descriptor(_descriptor(minimum="0.8.23"))

    class MissingMethod:
        def close(self) -> None:
            pass

    with pytest.raises(TypeError, match="get_digital_status_v2"):
        validate_declared_capabilities(_descriptor(), MissingMethod())


def test_digital_status_v2_method_does_not_create_an_implicit_capability() -> None:
    descriptor = _descriptor(capabilities=("scope.idn",))

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,MSO1"

        def close(self) -> None:
            pass

        def get_digital_status_v2(self, channel: int) -> ScopeDigitalChannelStatusV2:
            raise AssertionError(f"undeclared V2 method must not be called for D{channel}")

    validate_declared_capabilities(descriptor, Driver())
    assert "scope.digital_status_v2" not in descriptor.capabilities
    assert "scope.digital_waveform" not in descriptor.capabilities


def test_digital_status_v2_operation_is_a_stateful_exclusive_read() -> None:
    spec = require_operation_spec("scope.digital_status_v2")

    assert spec.instrument_kind == "scope"
    assert spec.required_capabilities == ("scope.digital_status_v2",)
    assert spec.effect == "stateful_read"
    assert spec.lease_mode == "exclusive"


def test_digital_status_v2_service_uses_only_v2_driver_method() -> None:
    expected = _complete_status(5)
    calls: list[tuple[str, int]] = []
    driver = SimpleNamespace(
        get_digital_status_v2=lambda channel: calls.append(("v2", channel)) or expected,
        get_digital_status=lambda channel: (_ for _ in ()).throw(
            AssertionError(f"legacy digital status was called for D{channel}")
        ),
    )
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="example.digital-status")),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.digital-status",
            capabilities=("scope.digital_status_v2",),
        ),
    )

    assert service.digital_status_v2(5) == expected
    assert calls == [("v2", 5)]


def test_digital_status_v2_service_rejects_invalid_or_wrong_channel_without_fallback() -> None:
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="example.digital-status")),
        logger=SimpleNamespace(),
        descriptor=SimpleNamespace(
            driver_id="example.digital-status",
            capabilities=("scope.digital_status_v2",),
        ),
    )
    with patch.object(service, "_open_scope") as open_scope:
        with pytest.raises(ConfigError, match="non-negative"):
            service.digital_status_v2(-1)
    open_scope.assert_not_called()

    service.session = SimpleNamespace(get_digital_status_v2=lambda _channel: _complete_status(6))
    with pytest.raises(DataError, match="wrong channel"):
        service.digital_status_v2(5)


def test_digital_status_v2_does_not_change_legacy_digital_status_route() -> None:
    legacy = ScopeDigitalChannelStatus(
        channel=1,
        group_start_channel=0,
        group_stop_channel=7,
        displayed=True,
        activity="TOGGLE",
        technology="TTL",
        threshold_v=1.4,
        threshold_coupled=False,
        hysteresis="NORMAL",
        deskew_s=0.0,
        size="SMALL",
        position_div=0.0,
        label="D1",
        label_enabled=True,
    )
    calls: list[tuple[str, int]] = []
    driver = SimpleNamespace(
        get_digital_status=lambda channel: calls.append(("legacy", channel)) or legacy,
        get_digital_status_v2=lambda channel: calls.append(("v2", channel))
        or _complete_status(channel),
    )
    service = ScopeService(
        config=SimpleNamespace(scope=SimpleNamespace(driver="example.digital-status")),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.digital-status",
            capabilities=("scope.digital_status", "scope.digital_status_v2"),
        ),
    )

    assert service.digital_status(1) is legacy
    assert calls == [("legacy", 1)]


def test_digital_status_v2_capability_explain_and_cli_output_are_additive() -> None:
    descriptor = SimpleNamespace(
        driver_id="example.digital-status",
        kind="scope",
        capabilities=("scope.digital_status_v2",),
    )
    explanation = explain_operation("scope.digital_status_v2", descriptor=descriptor)
    assert explanation.status == "supported"
    assert explanation.spec is not None
    assert explanation.spec.effect == "stateful_read"

    expected = ScopeDigitalChannelStatusV2(
        channel=0,
        activity="unknown",
        shared=ScopeDigitalSharedStatusV2(module_present=True),
        unavailable_fields=(
            "displayed",
            "position_div",
            "label",
            "label_enabled",
            "technology",
            "hysteresis",
            "pod",
            "shared.timing_calibration_s",
            "shared.size",
        ),
    )
    calls: list[int] = []
    service = SimpleNamespace(digital_status_v2=lambda channel: calls.append(channel) or expected)
    stdout = io.StringIO()
    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(["scope", "digital-status-v2", "--channel", "0"])

    assert code == 0
    assert calls == [0]
    assert stdout.getvalue().splitlines() == [
        "digital_v2.channel=0",
        "digital_v2.displayed=n/a",
        "digital_v2.position_div=n/a",
        "digital_v2.label=n/a",
        "digital_v2.label_enabled=n/a",
        "digital_v2.activity=unknown",
        "digital_v2.technology=n/a",
        "digital_v2.hysteresis=n/a",
        "digital_v2.pod=n/a",
        "digital_v2.shared.module_present=true",
        "digital_v2.shared.timing_calibration_s=n/a",
        "digital_v2.shared.size=n/a",
        "digital_v2.unavailable_fields=displayed,position_div,label,label_enabled,technology,hysteresis,pod,shared.timing_calibration_s,shared.size",
    ]

    stdout = io.StringIO()
    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(["scope", "digital-status-v2", "--channel", "0", "--json"])

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["schema"] == "wavebench.cli.result.v1"
    assert payload["result"]["channel"] == 0
    assert payload["result"]["activity"] == "unknown"
    assert payload["result"]["pod"] is None
    assert payload["result"]["shared"]["module_present"] is True
    assert payload["result"]["unavailable_fields"] == [
        "displayed",
        "position_div",
        "label",
        "label_enabled",
        "technology",
        "hysteresis",
        "pod",
        "shared.timing_calibration_s",
        "shared.size",
    ]


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
        driver_reference="example.digital-status",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )


def test_digital_status_v2_factory_latch_blocks_io_until_capability_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()
    errors: list[TransportIOError] = []

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,MSO1"

        def close(self) -> None:
            pass

        def get_digital_status_v2(self, channel: int) -> ScopeDigitalChannelStatusV2:
            return _complete_status(channel)

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


def test_digital_status_v2_factory_closes_on_missing_method_without_io(
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

    with pytest.raises(ConfigError, match="get_digital_status_v2"):
        _open_factory_descriptor()

    assert inner.queries == []
    assert inner.closed == 1
