from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wavebench.cli import main
from wavebench.cli_parser import build_parser
from wavebench.errors import AccessDeniedError, ConfigError, TransportIOError
from wavebench.instruments import InstrumentDescriptor, ScopeFocusDriverV2
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.contracts import ScopeDriver
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    SCOPE_STRICT_V2_CAPABILITIES,
    validate_scope_descriptor,
)
from wavebench.instruments.scope_extensions import (
    ScopeDescriptorExtensions,
    ScopeFocusBaseline,
    ScopeFocusChannelState,
    ScopeFocusProfileV2,
    ScopeFocusRequest,
    ScopeFocusRestoreResult,
    ScopeFocusState,
    ScopeFocusVerticalScale,
)
from wavebench.logging import CommandLogger
from wavebench.services.capability_explain import explain_operation
from wavebench.services.operation_specs import require_operation_spec
from wavebench.services.scope_service import ScopeService
from wavebench.transport.contracts import ReplayPolicy


def _profile(*, channels: tuple[int, ...] = (1, 2)) -> ScopeFocusProfileV2:
    return ScopeFocusProfileV2(
        analog_channels=channels,
        time_range_min_s=1e-9,
        time_range_max_s=100.0,
        time_range_abs_tolerance_s=1e-12,
        vertical_scale_min_v_per_div=1e-3,
        vertical_scale_max_v_per_div=10.0,
        vertical_scale_abs_tolerance_v_per_div=1e-6,
        vertical_range_abs_tolerance_v=1e-6,
        time_position_abs_tolerance_s=1e-12,
        position_abs_tolerance=1e-6,
        offset_abs_tolerance_v=1e-6,
        snapshot_max_steps=9,
        configure_max_steps=32,
        restore_max_steps=9,
        verify_max_steps=9,
    )


def _state(
    *,
    time_range_s: float = 0.01,
    enabled: tuple[bool, ...] = (True, True),
    ranges: tuple[float, ...] = (10.0, 20.0),
    scales: tuple[float, ...] = (1.0, 2.0),
    positions: tuple[float, ...] = (0.25, -0.5),
    offsets: tuple[float, ...] = (0.1, -0.2),
) -> ScopeFocusState:
    return ScopeFocusState(
        time_range_s=time_range_s,
        time_position_s=0.001,
        channels=tuple(
            ScopeFocusChannelState(channel, display, range_v, scale, position, offset)
            for channel, display, range_v, scale, position, offset in zip(
                range(1, len(enabled) + 1),
                enabled,
                ranges,
                scales,
                positions,
                offsets,
                strict=True,
            )
        ),
    )


def _descriptor(
    *,
    minimum: str = "0.8.26",
    capabilities: tuple[str, ...] = ("scope.idn", "scope.focus_configure_v2"),
    extensions: bool = True,
) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        driver_id="example.scope-focus-v2",
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
        scope_extensions=(ScopeDescriptorExtensions(focus_profile_v2=_profile()) if extensions else None),
    )


def test_focus_models_freeze_atomic_multi_channel_semantics() -> None:
    request = ScopeFocusRequest(
        channels=(1, 2),
        time_range_s=0.02,
        vertical_scales=(
            ScopeFocusVerticalScale(1, 0.5),
            ScopeFocusVerticalScale(2, 1.0),
        ),
        hide_others=True,
    )
    before = _state()
    after = _state(time_range_s=0.02, scales=(0.5, 1.0))
    profile = _profile()
    baseline = ScopeFocusBaseline(
        context_id="context",
        session_epoch="epoch",
        baseline_nonce="nonce",
        snapshot=before,
        restore_order=(
            "scope.timebase",
            "scope.channel_vertical",
            "scope.channel_display",
        ),
    )
    restored = ScopeFocusRestoreResult(
        "completed",
        baseline.restore_order,
        baseline.restore_order,
    )

    profile.validate_request(request)
    profile.validate_state(before)
    assert profile.restore_order_for(request) == baseline.restore_order
    assert profile.request_satisfied(after, request)
    assert profile.transition_matches(before, after, request)
    assert profile.states_equivalent(before, before)
    restored.validate_for(baseline)

    position_drift = replace(
        after,
        channels=(replace(after.channels[0], position=9.0), after.channels[1]),
    )
    assert not profile.transition_matches(before, position_drift, request)

    with pytest.raises(ValueError, match="ascending"):
        ScopeFocusRequest(channels=(2, 1))
    with pytest.raises(ValueError, match="selected channels"):
        ScopeFocusRequest(
            channels=(1,),
            vertical_scales=(ScopeFocusVerticalScale(2, 1.0),),
        )
    with pytest.raises(ValueError, match="outside"):
        profile.validate_request(ScopeFocusRequest(channels=(3,)))
    with pytest.raises(ValueError, match="fixed safe order"):
        ScopeFocusBaseline(
            "context",
            "epoch",
            "nonce",
            before,
            ("scope.channel_display", "scope.timebase"),
        )


def test_focus_is_an_additive_profile_gated_capability() -> None:
    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

        def get_focus_state_v2(self) -> ScopeFocusState:
            return _state()

        def configure_focus_v2(self, request, *, baseline) -> None:
            pass

        def restore_focus_v2(self, baseline):
            return ScopeFocusRestoreResult(
                "completed",
                baseline.restore_order,
                baseline.restore_order,
            )

    expected_methods = (
        "get_focus_state_v2",
        "configure_focus_v2",
        "restore_focus_v2",
    )
    assert isinstance(Driver(), ScopeFocusDriverV2)
    assert "configure_focus_v2" not in ScopeDriver.__dict__
    assert SCOPE_CAPABILITY_METHODS["scope.focus_configure_v2"] == expected_methods
    assert CAPABILITY_METHODS["scope.focus_configure_v2"] == expected_methods
    assert "scope.focus_configure_v2" in SCOPE_STRICT_V2_CAPABILITIES
    validate_declared_capabilities(_descriptor(), Driver())


def test_focus_descriptor_requires_version_profile_dependency_and_methods() -> None:
    with pytest.raises(ConfigError, match="scope focus V2 capability requires.*0.8.26"):
        validate_scope_descriptor(_descriptor(minimum="0.8.25"))
    with pytest.raises(ConfigError, match="scope_extensions.focus_profile_v2"):
        validate_scope_descriptor(_descriptor(extensions=False))
    with pytest.raises(ConfigError, match="scope.idn"):
        validate_scope_descriptor(_descriptor(capabilities=("scope.focus_configure_v2",)))

    class MissingMethods:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

    with pytest.raises(TypeError, match="get_focus_state_v2"):
        validate_declared_capabilities(_descriptor(), MissingMethods())


def test_focus_operation_spec_and_explain_are_fail_closed() -> None:
    spec = require_operation_spec("scope.focus_configure_v2")
    fields = (
        "scope.timebase",
        "scope.channel_vertical",
        "scope.channel_display",
    )

    assert spec.instrument_kind == "scope"
    assert spec.required_capabilities == ("scope.focus_configure_v2",)
    assert spec.effect == "write"
    assert spec.lease_mode == "exclusive"
    assert spec.changed_fields == (*fields, "scope.error_queue")
    assert spec.restore_coverage == "failure-cleanup-only"
    assert spec.verification_fields == fields
    assert spec.postcondition_fields == fields
    assert spec.cleanup_verification_fields == fields

    descriptor = SimpleNamespace(
        driver_id="example.scope-focus-v2",
        kind="scope",
        capabilities=("scope.focus_configure_v2",),
    )
    assert explain_operation("scope.focus_configure_v2", descriptor=descriptor).status == "supported"
    assert (
        explain_operation(
            "scope.focus_configure_v2",
            descriptor=descriptor,
            access="read_only",
        ).status
        == "access_denied"
    )


def test_scope_service_rejects_access_and_unknown_focus_channel_before_session() -> None:
    service = ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.scope-focus-v2", access="read_write"),
        ),
        logger=SimpleNamespace(),
        descriptor=_descriptor(),
    )

    with patch.object(service, "_open_scope") as open_scope:
        with pytest.raises(ConfigError, match="outside"):
            service.configure_focus_v2(ScopeFocusRequest(channels=(3,)))
    open_scope.assert_not_called()

    service.config.scope.access = "read_only"
    with patch.object(service, "_open_scope") as open_scope:
        with pytest.raises(AccessDeniedError, match="read_only"):
            service.configure_focus_v2(ScopeFocusRequest(channels=(1,)))
    open_scope.assert_not_called()


def test_scope_focus_cli_builds_typed_multi_channel_request() -> None:
    argv = [
        "scope",
        "focus",
        "--channel",
        "2",
        "--channel",
        "1",
        "--time-range",
        "0.02",
        "--vertical-scale",
        "1=0.5",
        "--vertical-scale",
        "2=1.0",
        "--hide-others",
    ]
    args = build_parser().parse_args(argv)
    assert args.command == "focus"
    assert args.channels == [2, 1]

    calls: list[ScopeFocusRequest] = []
    scope_payload = {
        "schema": "wavebench.scope.result.v1",
        "result": {"write_performed": True},
        "diagnostics": {"schema": "wavebench.scope.operation.v1"},
        "observed_state": None,
    }

    class Result:
        def as_dict(self) -> dict[str, object]:
            return scope_payload

    class Service:
        def configure_focus_v2(self, request, *, error_check=None):
            assert error_check is None
            calls.append(request)
            return Result()

    stdout = io.StringIO()
    with patch("wavebench.cli._load_service", return_value=Service()), redirect_stdout(stdout):
        code = main(argv)

    assert code == 0
    assert calls == [
        ScopeFocusRequest(
            channels=(1, 2),
            time_range_s=0.02,
            vertical_scales=(
                ScopeFocusVerticalScale(1, 0.5),
                ScopeFocusVerticalScale(2, 1.0),
            ),
            hide_others=True,
        )
    ]
    assert json.loads(stdout.getvalue()) == scope_payload


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
        driver_reference="example.scope-focus-v2",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )


def test_focus_factory_latch_and_missing_method_are_zero_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()
    errors: list[TransportIOError] = []

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

        def get_focus_state_v2(self) -> ScopeFocusState:
            return _state()

        def configure_focus_v2(self, request, *, baseline) -> None:
            pass

        def restore_focus_v2(self, baseline):
            return ScopeFocusRestoreResult(
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

    with pytest.raises(ConfigError, match="get_focus_state_v2"):
        _open_factory_descriptor()
    assert inner.queries == []
    assert inner.closed == 1
