from __future__ import annotations

from types import SimpleNamespace

import pytest

from wavebench.errors import SessionHealthError
from wavebench.logging import CommandLogger
from wavebench.services.scope_service import ScopeService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState, SessionHealth


class _InnerTransport:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, command: str, *, replay: ReplayPolicy) -> str:
        assert replay is ReplayPolicy.NO_REPLAY
        self.queries.append(command)
        if command == "*IDN?":
            return "VENDOR,SCOPE,SERIAL,FIRMWARE"
        return "waveform"

    def close(self) -> None:
        pass


class _ScopeDriver:
    def __init__(self, transport: GuardedAuditedTransport) -> None:
        self.transport = transport

    def idn(self) -> str:
        return self.transport.query("*IDN?", replay=ReplayPolicy.NO_REPLAY)

    def fetch_waveform(self, *, channel: int, points: str, check_errors: bool) -> str:
        assert channel == 1
        assert points == "DEF"
        assert check_errors is False
        return self.transport.query("WAV?", replay=ReplayPolicy.NO_REPLAY)

    def close(self) -> None:
        self.transport.close()


def _service(
    *,
    state: InstrumentSessionState,
) -> tuple[ScopeService, _InnerTransport]:
    inner = _InnerTransport()
    guarded = GuardedAuditedTransport(inner, session_state=state)
    driver = _ScopeDriver(guarded)
    service = ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(
                driver="example.scope",
                access="read_write",
                check_errors=False,
            ),
            connection=SimpleNamespace(timeout_ms=1000),
            waveform=SimpleNamespace(format="real", byte_order="lsbf", points="DEF"),
        ),
        logger=CommandLogger(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.scope",
            capabilities=("scope.idn", "scope.fetch_waveform"),
        ),
        transport=guarded,
        session_state=state,
    )
    return service, inner


def test_fetch_verifies_required_identity_before_waveform_query() -> None:
    state = InstrumentSessionState(epoch_id="epoch-preflight")
    service, inner = _service(state=state)

    assert service.fetch_waveform(1) == "waveform"

    assert inner.queries == ["*IDN?", "WAV?"]
    assert state.verified_fields == {"scope.identity"}


def test_fetch_reuses_identity_evidence_from_same_epoch() -> None:
    state = InstrumentSessionState(
        epoch_id="epoch-verified",
        verified_fields={"scope.identity"},
    )
    service, inner = _service(state=state)

    assert service.fetch_waveform(1) == "waveform"

    assert inner.queries == ["WAV?"]


def test_fetch_rejects_uncertain_session_before_any_transport_io() -> None:
    state = InstrumentSessionState(epoch_id="epoch-uncertain")
    state.degrade(SessionHealth.UNCERTAIN, reason="test_uncertain")
    service, inner = _service(state=state)

    with pytest.raises(SessionHealthError) as raised:
        service.fetch_waveform(1)

    assert raised.value.io_kind == "operation_preflight"
    assert inner.queries == []
