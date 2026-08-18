from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wavebench.errors import ConfigError
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState, SessionHealth


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        source=SimpleNamespace(
            driver="example.source",
            resource="TCPIP::source::INSTR",
            access="read_write",
            check_errors=True,
            options={},
            default_channel=1,
        ),
        connection=SimpleNamespace(
            backend="lan",
            timeout_ms=1000,
            opc_timeout_ms=1000,
            read_retry_attempts=0,
            read_retry_delay_ms=0,
        ),
    )


class _Inner:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def test_service_session_state_alias_cannot_be_rebound() -> None:
    state = InstrumentSessionState(epoch_id="epoch-a")
    service = SourceService(
        config=_config(),
        logger=CommandLogger(),
        session_state=state,
    )

    with pytest.raises(AttributeError, match="read-only"):
        service.session_state = InstrumentSessionState(epoch_id="epoch-b")


def test_service_reopen_requires_close_then_binds_a_new_epoch() -> None:
    old_state = InstrumentSessionState(epoch_id="epoch-old")
    old_inner = _Inner()
    old_transport = GuardedAuditedTransport(old_inner, session_state=old_state)
    old_driver = SimpleNamespace(close=old_transport.close)
    service = SourceService(
        config=_config(),
        logger=CommandLogger(),
        session=old_driver,
        transport=old_transport,
        session_state=old_state,
    )

    with pytest.raises(ConfigError, match="already open"):
        service.open_session()
    assert old_state.health is SessionHealth.HEALTHY
    assert old_inner.close_count == 0

    old_transport.close()
    new_state = InstrumentSessionState(epoch_id="epoch-new")
    new_inner = _Inner()
    new_transport = GuardedAuditedTransport(new_inner, session_state=new_state)
    opened = SimpleNamespace(
        descriptor=SimpleNamespace(driver_id="example.source"),
        driver=SimpleNamespace(close=new_transport.close),
        transport=new_transport,
        session_state=new_state,
    )
    with patch(
        "wavebench.services.source_service.open_instrument_driver",
        return_value=opened,
    ):
        assert service.open_session() is opened.driver

    assert old_state.health is SessionHealth.CLOSED
    assert old_state.epoch_id != new_state.epoch_id
    assert service.session_state is new_state
    assert service.transport is new_transport
    new_transport.close()
