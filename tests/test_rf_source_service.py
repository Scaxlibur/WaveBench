from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    RfSourceConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.errors import AccessDeniedError, ConfigError
from wavebench.instruments.rf_source_extensions import (
    RfModulationState,
    RfObserved,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseState,
    RfSourceSnapshot,
    RfSweepState,
)
from wavebench.logging import CommandLogger
from wavebench.services.rf_source_service import RfSourceService
from wavebench.transport.session import InstrumentSessionState, SessionHealth


class FakeRfDriver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def close(self) -> None:
        self.calls.append("close")

    def idn(self) -> str:
        self.calls.append("idn")
        return "EXAMPLE,RF1,0,1"

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        self.calls.append("snapshot")
        return _snapshot()


def _snapshot() -> RfSourceSnapshot:
    return RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(1_000_000.0),
                power_dbm=RfObserved.value_of(-30.0),
                output_enabled=RfObserved.value_of(False),
                modulation=RfObserved.value_of(RfModulationState.DISABLED),
                pulse=RfObserved.value_of(RfPulseState.DISABLED),
                sweep=RfObserved.value_of(RfSweepState.DISABLED),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
    )


def _config(*, access: str = "read_only") -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "dmax"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("test.toml"),
        rf_source=RfSourceConfig(
            driver="example.rf1",
            resource="TCPIP::rf::INSTR",
            access=access,  # type: ignore[arg-type]
        ),
    )


def _descriptor(*capabilities: str) -> SimpleNamespace:
    return SimpleNamespace(driver_id="example.rf1", capabilities=capabilities)


def test_idn_and_snapshot_are_one_shot_read_only_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = FakeRfDriver()
    service = RfSourceService(
        config=_config(),
        logger=CommandLogger(),
        descriptor=_descriptor("rf_source.idn", "rf_source.snapshot"),
    )
    monkeypatch.setattr(service, "_open_rf_source", lambda: driver)

    assert service.idn() == "EXAMPLE,RF1,0,1"
    assert service.snapshot().ports[0].port_id == "rf_out"
    assert driver.calls == ["idn", "close", "snapshot", "close"]


def test_snapshot_capability_and_access_are_checked_before_opening_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RfSourceService(
        config=_config(),
        logger=CommandLogger(),
        descriptor=_descriptor("rf_source.idn"),
    )
    opened = False

    def fail_open() -> FakeRfDriver:
        nonlocal opened
        opened = True
        return FakeRfDriver()

    monkeypatch.setattr(service, "_open_rf_source", fail_open)
    with pytest.raises(ConfigError, match="rf_source.snapshot"):
        service.snapshot()
    assert opened is False

    disabled = RfSourceService(
        config=_config(access="disabled"),
        logger=CommandLogger(),
        descriptor=_descriptor("rf_source.idn", "rf_source.snapshot"),
    )
    with patch.object(disabled, "_open_rf_source") as open_disabled:
        with pytest.raises(AccessDeniedError, match="rf_source.idn"):
            disabled.idn()
    open_disabled.assert_not_called()


def test_snapshot_rejects_nonhealthy_bound_session_before_driver_call() -> None:
    driver = FakeRfDriver()
    service = RfSourceService(
        config=_config(),
        logger=CommandLogger(),
        session=driver,
        descriptor=_descriptor("rf_source.idn", "rf_source.snapshot"),
        session_state=InstrumentSessionState(health=SessionHealth.UNCERTAIN),
    )

    with pytest.raises(ConfigError, match="healthy session"):
        service.snapshot()

    assert driver.calls == []


def test_one_shot_service_passes_owned_lease_to_factory() -> None:
    driver = FakeRfDriver()
    descriptor = _descriptor("rf_source.idn", "rf_source.snapshot")
    opened = SimpleNamespace(
        descriptor=descriptor,
        transport=None,
        session_state=None,
        driver=driver,
    )
    service = RfSourceService(
        config=_config(),
        logger=CommandLogger(),
        descriptor=descriptor,
    )

    with patch(
        "wavebench.services.rf_source_service.open_instrument_driver",
        return_value=opened,
    ) as factory:
        assert service.idn() == "EXAMPLE,RF1,0,1"

    lease = factory.call_args.kwargs["lease"]
    assert lease.resource == "tcpip::rf::instr"
    assert driver.calls == ["idn", "close"]
