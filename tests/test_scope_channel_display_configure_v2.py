from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import asdict, replace
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wavebench.cli import main
from wavebench.cli_parser import build_parser
from wavebench.errors import AccessDeniedError, ConfigError, TransportIOError
from wavebench.instruments import InstrumentDescriptor, ScopeChannelDisplayDriverV2
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.contracts import ScopeDriver
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    SCOPE_STRICT_V2_CAPABILITIES,
    validate_scope_descriptor,
)
from wavebench.instruments.scope_extensions import (
    ScopeChannelDisplayBaseline,
    ScopeChannelDisplayProfileV2,
    ScopeChannelDisplayRequest,
    ScopeChannelDisplayRestoreResult,
    ScopeChannelDisplayResult,
    ScopeChannelDisplayState,
    ScopeDescriptorExtensions,
)
from wavebench.logging import CommandLogger
from wavebench.services.capability_explain import explain_operation
from wavebench.services.operation_specs import require_operation_spec
from wavebench.services.scope_service import ScopeService
from wavebench.transport.contracts import ReplayPolicy


def _profile() -> ScopeChannelDisplayProfileV2:
    return ScopeChannelDisplayProfileV2(
        analog_channels=(1, 2),
        snapshot_max_steps=1,
        configure_max_steps=2,
        restore_max_steps=1,
        verify_max_steps=1,
    )


def _descriptor(
    *,
    minimum: str = "0.8.24",
    capabilities: tuple[str, ...] = (
        "scope.idn",
        "scope.channel_display_configure_v2",
    ),
    extensions: bool = True,
) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        driver_id="example.channel-display-v2",
        kind="scope",
        display_name="Example Scope",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities,
        idn_patterns=("EXAMPLE,EX1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda _context: object(),
        wavebench_min_version=minimum,
        scope_extensions=(
            ScopeDescriptorExtensions(channel_display_profile_v2=_profile())
            if extensions
            else None
        ),
    )


def test_channel_display_models_and_profile_freeze_single_field_semantics() -> None:
    request = ScopeChannelDisplayRequest(channel=2, enabled=True)
    before = ScopeChannelDisplayState(channel=2, enabled=False)
    after = ScopeChannelDisplayState(channel=2, enabled=True)
    result = ScopeChannelDisplayResult(request, before, after, write_performed=True)
    baseline = ScopeChannelDisplayBaseline(
        context_id="context",
        session_epoch="epoch",
        baseline_nonce="nonce",
        snapshot=before,
        restore_order=("scope.channel_display",),
    )
    restored = ScopeChannelDisplayRestoreResult(
        "completed",
        baseline.restore_order,
        baseline.restore_order,
    )

    assert asdict(result) == {
        "request": {"channel": 2, "enabled": True},
        "before": {"channel": 2, "enabled": False},
        "after": {"channel": 2, "enabled": True},
        "write_performed": True,
    }
    _profile().validate_request(request)
    _profile().validate_state(after, channel=2)
    restored.validate_for(baseline)

    with pytest.raises(ValueError, match="integer"):
        ScopeChannelDisplayRequest(channel=True, enabled=True)
    with pytest.raises(TypeError, match="bool"):
        ScopeChannelDisplayState(channel=1, enabled=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ascending"):
        ScopeChannelDisplayProfileV2((2, 1), 1, 2, 1, 1)
    with pytest.raises(ValueError, match="outside"):
        _profile().validate_request(ScopeChannelDisplayRequest(channel=3, enabled=True))
    with pytest.raises(ValueError, match="write_performed"):
        ScopeChannelDisplayResult(request, before, after, write_performed=False)


def test_channel_display_is_an_additive_profile_gated_capability() -> None:
    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

        def get_channel_display_state_v2(self, channel: int) -> ScopeChannelDisplayState:
            return ScopeChannelDisplayState(channel, False)

        def configure_channel_display_v2(self, request, *, baseline) -> None:
            pass

        def restore_channel_display_v2(self, baseline):
            return ScopeChannelDisplayRestoreResult(
                "completed",
                baseline.restore_order,
                baseline.restore_order,
            )

    assert isinstance(Driver(), ScopeChannelDisplayDriverV2)
    assert "configure_channel_display_v2" not in ScopeDriver.__dict__
    expected_methods = (
        "get_channel_display_state_v2",
        "configure_channel_display_v2",
        "restore_channel_display_v2",
    )
    assert SCOPE_CAPABILITY_METHODS["scope.channel_display_configure_v2"] == expected_methods
    assert CAPABILITY_METHODS["scope.channel_display_configure_v2"] == expected_methods
    assert "scope.channel_display_configure_v2" in SCOPE_STRICT_V2_CAPABILITIES
    validate_declared_capabilities(_descriptor(), Driver())


def test_channel_display_descriptor_requires_floor_profile_dependency_and_methods() -> None:
    with pytest.raises(ConfigError, match="scope portability V2 capabilities require.*0.8.24"):
        validate_scope_descriptor(_descriptor(minimum="0.8.23"))
    with pytest.raises(ConfigError, match="scope_extensions.channel_display_profile_v2"):
        validate_scope_descriptor(_descriptor(extensions=False))
    with pytest.raises(ConfigError, match="scope.idn"):
        validate_scope_descriptor(
            _descriptor(capabilities=("scope.channel_display_configure_v2",))
        )

    class MissingMethods:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

    with pytest.raises(TypeError, match="get_channel_display_state_v2"):
        validate_declared_capabilities(_descriptor(), MissingMethods())


def test_channel_display_operation_spec_and_explain_are_fail_closed() -> None:
    spec = require_operation_spec("scope.channel_display_configure_v2")

    assert spec.instrument_kind == "scope"
    assert spec.required_capabilities == ("scope.channel_display_configure_v2",)
    assert spec.effect == "write"
    assert spec.lease_mode == "exclusive"
    assert spec.changed_fields == ("scope.channel_display", "scope.error_queue")
    assert spec.restore_coverage == "failure-cleanup-only"
    assert spec.verification_fields == ("scope.channel_display",)
    assert spec.postcondition_fields == ("scope.channel_display",)
    assert spec.cleanup_verification_fields == ("scope.channel_display",)

    descriptor = SimpleNamespace(
        driver_id="example.channel-display-v2",
        kind="scope",
        capabilities=("scope.channel_display_configure_v2",),
    )
    assert explain_operation(
        "scope.channel_display_configure_v2",
        descriptor=descriptor,
    ).status == "supported"
    assert explain_operation(
        "scope.channel_display_configure_v2",
        descriptor=descriptor,
        access="read_only",
    ).status == "access_denied"


def test_scope_service_rejects_access_and_unknown_channel_before_opening_session() -> None:
    request = ScopeChannelDisplayRequest(channel=3, enabled=True)
    service = ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(
                driver="example.channel-display-v2",
                access="read_write",
            ),
        ),
        logger=SimpleNamespace(),
        descriptor=_descriptor(),
    )

    with patch.object(service, "_open_scope") as open_scope:
        with pytest.raises(ConfigError, match="outside"):
            service.configure_channel_display_v2(request)
    open_scope.assert_not_called()

    service.config.scope.access = "read_only"
    with patch.object(service, "_open_scope") as open_scope:
        with pytest.raises(AccessDeniedError, match="read_only"):
            service.configure_channel_display_v2(
                ScopeChannelDisplayRequest(channel=1, enabled=True)
            )
    open_scope.assert_not_called()


def test_scope_display_cli_builds_typed_request_and_emits_versioned_result() -> None:
    args = build_parser().parse_args(["scope", "display", "--channel", "2", "on"])
    assert args.command == "display"
    assert args.channel == 2
    assert args.state == "on"

    calls: list[ScopeChannelDisplayRequest] = []
    scope_payload = {
        "schema": "wavebench.scope.result.v1",
        "result": {
            "request": {"channel": 2, "enabled": True},
            "write_performed": True,
        },
        "diagnostics": {"schema": "wavebench.scope.operation.v1"},
        "observed_state": None,
    }

    class Result:
        def as_dict(self) -> dict[str, object]:
            return scope_payload

    class Service:
        def configure_channel_display_v2(self, request, *, error_check=None):
            assert error_check is None
            calls.append(request)
            return Result()

    stdout = io.StringIO()
    with patch("wavebench.cli._load_service", return_value=Service()), redirect_stdout(stdout):
        code = main(["scope", "display", "--channel", "2", "on"])

    assert code == 0
    assert calls == [ScopeChannelDisplayRequest(channel=2, enabled=True)]
    assert json.loads(stdout.getvalue()) == scope_payload

    stdout = io.StringIO()
    with patch("wavebench.cli._load_service", return_value=Service()), redirect_stdout(stdout):
        code = main(["scope", "display", "--channel", "2", "on", "--json"])

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["schema"] == "wavebench.cli.result.v1"
    assert payload["result"] == scope_payload


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
        driver_reference="example.channel-display-v2",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )


def test_channel_display_factory_latch_and_missing_method_are_zero_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()
    errors: list[TransportIOError] = []

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

        def get_channel_display_state_v2(self, channel: int) -> ScopeChannelDisplayState:
            return ScopeChannelDisplayState(channel, False)

        def configure_channel_display_v2(self, request, *, baseline) -> None:
            pass

        def restore_channel_display_v2(self, baseline):
            return ScopeChannelDisplayRestoreResult(
                "completed",
                baseline.restore_order,
                baseline.restore_order,
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

    assert [error.reason_code for error in errors] == ["factory_construction_pending"]
    assert errors[0].attempts == 0
    assert inner.queries == []
    assert opened.transport is not None
    assert opened.transport.query("*IDN?") == "ok"
    assert inner.queries == ["*IDN?"]

    inner = _FactoryTransport()

    class MissingMethods:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

    missing = replace(
        _descriptor(),
        factory=lambda context: (context.open_transport(), MissingMethods())[1],
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda _reference, expected_kind: missing,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **_kwargs: inner)

    with pytest.raises(ConfigError, match="get_channel_display_state_v2"):
        _open_factory_descriptor()
    assert inner.queries == []
    assert inner.closed == 1
