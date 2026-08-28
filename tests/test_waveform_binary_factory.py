from __future__ import annotations

import pytest

from wavebench.errors import ConfigError, TransportIOError
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.scope_extensions import (
    ScopeDescriptorExtensions,
    ScopeWaveformBinaryOperationProfile,
    ScopeWaveformBinaryProfile,
)
from wavebench.logging import CommandLogger
from wavebench.transport.contracts import BinaryResponseFraming, ReplayPolicy


class _InnerTransport:
    resource = "TCPIP::example::INSTR"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.writes: list[str] = []
        self.closed = 0

    def record_event(self, direction: str, text: str) -> None:
        pass

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        self.queries.append(command)
        return "ok"

    def query_float_list(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> list[float]:
        self.queries.append(command)
        return [1.0]

    def query_bin_block(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> bytes:
        self.queries.append(command)
        return b""

    def query_binary(self, *args, **kwargs):
        self.queries.append("BINARY")
        raise AssertionError("construction latch should reject before the backend")

    def query_opc(self, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        self.queries.append("*OPC?")
        return "1"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def write_bytes(self, command: bytes) -> None:
        self.writes.append("BINARY_WRITE")

    def close(self) -> None:
        self.closed += 1


def _profile() -> ScopeWaveformBinaryProfile:
    return ScopeWaveformBinaryProfile(
        operations=(
            ScopeWaveformBinaryOperationProfile(
                operation_kind="fetch",
                response_max_bytes=1_024,
                operation_max_bytes=4_096,
                query_max_count=4,
                resynchronization_max_bytes=0,
                restore_order=("scope.waveform_source",),
                snapshot_max_steps=1,
                restore_max_steps=1,
                verify_max_steps=1,
            ),
        )
    )


def _descriptor(*, factory, profile: ScopeWaveformBinaryProfile | None) -> InstrumentDescriptor:
    capabilities = ("scope.idn", "scope.fetch_waveform") if profile is not None else ("scope.idn",)
    return InstrumentDescriptor(
        driver_id="example.waveform",
        kind="scope",
        display_name="Example",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities,
        idn_patterns=("EXAMPLE",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=factory,
        wavebench_min_version="0.8.24",
        scope_extensions=(
            ScopeDescriptorExtensions(waveform_binary_profile=profile)
            if profile is not None
            else None
        ),
    )


class _BoundedDriver:
    def idn(self) -> str:
        return "EXAMPLE,EX1"

    def close(self) -> None:
        pass

    def snapshot_waveform_transfer_state(self, fields):
        return object()

    def restore_waveform_transfer_state(self, baseline):
        return object()

    def verify_waveform_transfer_state_restored(self, baseline):
        return object()

    def fetch_waveform_bounded(self, channel, points="dmax", *, baseline):
        return object()


def _open() -> object:
    return open_instrument_driver(
        driver_reference="example.waveform",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=1,
        logger=CommandLogger(),
    )


def test_opt_in_factory_latch_blocks_all_instrument_io_until_validation(monkeypatch) -> None:
    inner = _InnerTransport()
    errors: list[TransportIOError] = []

    def factory(context):
        transport = context.open_transport()
        calls = (
            lambda: transport.write("STOP"),
            lambda: transport.write_bytes(b"x"),
            lambda: transport.query("*IDN?"),
            lambda: transport.query_float_list("MEAS?"),
            lambda: transport.query_bin_block("WAV?"),
            lambda: transport.query_binary(
                "WAV?",
                framing=BinaryResponseFraming.DEFINITE_BLOCK,
                max_bytes=1,
            ),
            lambda: transport.query_opc(),
        )
        for call in calls:
            with pytest.raises(TransportIOError) as raised:
                call()
            errors.append(raised.value)
        return _BoundedDriver()

    descriptor = _descriptor(factory=factory, profile=_profile())
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **kwargs: inner)
    monkeypatch.setattr(
        "wavebench.instruments.factory._validate_bounded_binary_transport",
        lambda **kwargs: None,
    )

    opened = _open()

    assert len(errors) == 7
    assert all(error.reason_code == "factory_construction_pending" for error in errors)
    assert all(error.attempts == 0 for error in errors)
    assert inner.queries == []
    assert inner.writes == []
    assert opened.transport._has_verified_bounded_binary_backend()
    assert opened.transport.query("*IDN?") == "ok"
    assert inner.queries == ["*IDN?"]


def test_opt_in_factory_validation_failure_closes_without_probe(monkeypatch) -> None:
    inner = _InnerTransport()

    class MissingBoundedDriver:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

        def snapshot_waveform_transfer_state(self, fields):
            return object()

        def restore_waveform_transfer_state(self, baseline):
            return object()

        def verify_waveform_transfer_state_restored(self, baseline):
            return object()

    descriptor = _descriptor(
        factory=lambda context: (context.open_transport(), MissingBoundedDriver())[1],
        profile=_profile(),
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **kwargs: inner)

    with pytest.raises(ConfigError, match="fetch_waveform_bounded"):
        _open()

    assert inner.queries == []
    assert inner.writes == []
    assert inner.closed == 1


def test_opt_in_factory_rejects_untrusted_backend_without_probe(monkeypatch) -> None:
    inner = _InnerTransport()
    descriptor = _descriptor(
        factory=lambda context: (context.open_transport(), _BoundedDriver())[1],
        profile=_profile(),
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **kwargs: inner)

    with pytest.raises(ConfigError, match="bounded PyVISA or RsInstrument"):
        _open()

    assert inner.queries == []
    assert inner.writes == []
    assert inner.closed == 1


def test_legacy_factory_is_not_latched(monkeypatch) -> None:
    inner = _InnerTransport()

    class LegacyDriver:
        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def close(self) -> None:
            pass

    def factory(context):
        context.open_transport().query("*IDN?")
        return LegacyDriver()

    descriptor = _descriptor(factory=factory, profile=None)
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **kwargs: inner)

    _open()

    assert inner.queries == ["*IDN?"]
