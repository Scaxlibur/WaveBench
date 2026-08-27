from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    RfPortSafetyConfig,
    RfSourceConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.errors import AccessDeniedError, ConfigError
from wavebench.instruments.rf_source_extensions import (
    RF_SOURCE_CONTRACT_VERSION,
    RfAvailability,
    RfModulationState,
    RfObserved,
    RfCwProfile,
    RfCwRequest,
    RfCwResult,
    RfFeature,
    RfFeatureCapability,
    RfFeatureDirection,
    RfOutputPortProfile,
    RfOutputProfile,
    RfOutputRequest,
    RfOutputResult,
    RfPortSnapshot,
    RfProtectionConditionPolicy,
    RfProtectionStatus,
    RfPulseState,
    RfReasonCode,
    RfSourceDescriptorExtensions,
    RfSourceSnapshot,
    RfSourceTopology,
    RfSweepState,
)
from wavebench.logging import CommandLogger
from wavebench.services.rf_source_service import RfSourceService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
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


class FakeRfWriteDriver:
    def __init__(
        self,
        snapshots: list[RfSourceSnapshot],
        *,
        raise_after_write: bool = False,
    ) -> None:
        self.snapshots = list(snapshots)
        self.raise_after_write = raise_after_write
        self.calls: list[str] = []
        self.requests: list[RfCwRequest] = []

    def close(self) -> None:
        self.calls.append("close")

    def idn(self) -> str:
        self.calls.append("idn")
        return "EXAMPLE,RF1,0,1"

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        self.calls.append("snapshot")
        if not self.snapshots:
            raise AssertionError("unexpected RF snapshot query")
        return self.snapshots.pop(0)

    def configure_cw(self, request: RfCwRequest) -> None:
        self.calls.append("configure_cw")
        self.requests.append(request)
        if self.raise_after_write:
            raise ConfigError("fake CW write failed after transmission")


class FakeRfOutputDriver:
    def __init__(
        self,
        snapshots: list[RfSourceSnapshot],
        *,
        raise_after_enable: bool = False,
        raise_after_disable: bool = False,
    ) -> None:
        self.snapshots = list(snapshots)
        self.raise_after_enable = raise_after_enable
        self.raise_after_disable = raise_after_disable
        self.calls: list[str] = []
        self.output_requests: list[RfOutputRequest] = []

    def close(self) -> None:
        self.calls.append("close")

    def idn(self) -> str:
        self.calls.append("idn")
        return "EXAMPLE,RF1,0,1"

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        self.calls.append("snapshot")
        if not self.snapshots:
            raise AssertionError("unexpected RF snapshot query")
        return self.snapshots.pop(0)

    def set_rf_output(self, request: RfOutputRequest) -> None:
        self.calls.append("set_rf_output")
        self.output_requests.append(request)
        if request.enabled and self.raise_after_enable:
            raise ConfigError("fake RF ON failed after transmission")
        if not request.enabled and self.raise_after_disable:
            raise ConfigError("fake RF OFF failed after transmission")


class _GuardedOutputTransport:
    resource = "fake-rf-output"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.writes: list[str] = []

    def record_event(self, direction: str, text: str) -> None:
        del direction, text

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        del replay
        self.queries.append(command)
        return "ok"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def close(self) -> None:
        return None


class GuardedRfOutputDriver:
    def __init__(self, snapshots: list[RfSourceSnapshot], state: InstrumentSessionState) -> None:
        self.inner = _GuardedOutputTransport()
        self.transport = GuardedAuditedTransport(self.inner, session_state=state)
        self.snapshots = list(snapshots)
        self.output_requests: list[RfOutputRequest] = []

    def close(self) -> None:
        self.transport.close()

    def idn(self) -> str:
        return "EXAMPLE,RF1,0,1"

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        self.transport.query("RF:SNAPSHOT?")
        if not self.snapshots:
            raise AssertionError("unexpected RF snapshot query")
        return self.snapshots.pop(0)

    def set_rf_output(self, request: RfOutputRequest) -> None:
        self.output_requests.append(request)
        self.transport.write("RF:OUTPUT ON" if request.enabled else "RF:OUTPUT OFF")


def _snapshot(
    *,
    frequency_hz: float = 1_000_000.0,
    power_dbm: float = -30.0,
    output_enabled: bool = False,
    modulation: RfModulationState = RfModulationState.DISABLED,
    pulse: RfPulseState = RfPulseState.DISABLED,
    sweep: RfSweepState = RfSweepState.DISABLED,
    protection_codes: tuple[str, ...] = (),
) -> RfSourceSnapshot:
    return RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(frequency_hz),
                power_dbm=RfObserved.value_of(power_dbm),
                output_enabled=RfObserved.value_of(output_enabled),
                modulation=RfObserved.value_of(modulation),
                pulse=RfObserved.value_of(pulse),
                sweep=RfObserved.value_of(sweep),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=protection_codes)),
    )


def _config(
    *,
    access: str = "read_only",
    safety_ports: tuple[RfPortSafetyConfig, ...] = (),
) -> WaveBenchConfig:
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
            safety_ports=safety_ports,
        ),
    )


def _descriptor(*capabilities: str) -> SimpleNamespace:
    return SimpleNamespace(driver_id="example.rf1", capabilities=capabilities)


def _cw_descriptor(
    *capabilities: str,
    frequency_configurable: bool = True,
    power_configurable: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf1",
        capabilities=capabilities,
        rf_source_extensions=RfSourceDescriptorExtensions(
            contract_version=RF_SOURCE_CONTRACT_VERSION,
            topology=RfSourceTopology(
                (
                    RfOutputPortProfile(
                        port_id="rf_out",
                        frequency_min_hz=9_000.0,
                        frequency_max_hz=3_000_000_000.0,
                        power_min_dbm=-110.0,
                        power_max_dbm=20.0,
                        power_reference_impedance_ohm=50.0,
                    ),
                )
            ),
            features=(
                RfFeatureCapability(
                    feature=RfFeature.CW,
                    directions=(RfFeatureDirection.CONFIGURE, RfFeatureDirection.READ),
                    port_ids=("rf_out",),
                    profile=RfCwProfile(
                        frequency_readable=True,
                        power_readable=True,
                        frequency_configurable=frequency_configurable,
                        power_configurable=power_configurable,
                    ),
                ),
            ),
            protection_conditions=(
                RfProtectionConditionPolicy("overtemperature", True),
            ),
        ),
    )


def _cw_service(
    snapshots: list[RfSourceSnapshot],
    *,
    access: str = "read_write",
    descriptor: SimpleNamespace | None = None,
    raise_after_write: bool = False,
) -> tuple[RfSourceService, FakeRfWriteDriver]:
    driver = FakeRfWriteDriver(snapshots, raise_after_write=raise_after_write)
    service = RfSourceService(
        config=_config(access=access),
        logger=CommandLogger(),
        session=driver,
        descriptor=descriptor
        or _cw_descriptor(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.cw_configure",
        ),
        session_state=InstrumentSessionState(),
    )
    return service, driver


def _output_descriptor(*capabilities: str) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf1",
        capabilities=capabilities,
        rf_source_extensions=RfSourceDescriptorExtensions(
            contract_version=RF_SOURCE_CONTRACT_VERSION,
            topology=RfSourceTopology(
                (
                    RfOutputPortProfile(
                        port_id="rf_out",
                        frequency_min_hz=9_000.0,
                        frequency_max_hz=3_000_000_000.0,
                        power_min_dbm=-110.0,
                        power_max_dbm=20.0,
                        power_reference_impedance_ohm=50.0,
                    ),
                )
            ),
            features=(
                RfFeatureCapability(
                    feature=RfFeature.OUTPUT,
                    directions=(
                        RfFeatureDirection.DISABLE,
                        RfFeatureDirection.ENABLE,
                        RfFeatureDirection.READ,
                    ),
                    port_ids=("rf_out",),
                    profile=RfOutputProfile(output_readable=True),
                ),
            ),
            protection_conditions=(
                RfProtectionConditionPolicy("overtemperature", True),
                RfProtectionConditionPolicy("status_notice", False),
            ),
        ),
    )


def _output_safety_port(*, termination_ohm: float = 50.0) -> RfPortSafetyConfig:
    return RfPortSafetyConfig(
        port_id="rf_out",
        minimum_frequency_hz=9_000.0,
        maximum_frequency_hz=3_000_000_000.0,
        maximum_power_dbm=0.0,
        actual_termination_ohm=termination_ohm,
    )


def _output_service(
    snapshots: list[RfSourceSnapshot],
    *,
    access: str = "read_write",
    safety_ports: tuple[RfPortSafetyConfig, ...] | None = None,
    descriptor: SimpleNamespace | None = None,
    raise_after_enable: bool = False,
    raise_after_disable: bool = False,
) -> tuple[RfSourceService, FakeRfOutputDriver]:
    driver = FakeRfOutputDriver(
        snapshots,
        raise_after_enable=raise_after_enable,
        raise_after_disable=raise_after_disable,
    )
    service = RfSourceService(
        config=_config(
            access=access,
            safety_ports=(
                (_output_safety_port(),)
                if safety_ports is None
                else safety_ports
            ),
        ),
        logger=CommandLogger(),
        session=driver,
        descriptor=descriptor
        or _output_descriptor(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.output",
        ),
        session_state=InstrumentSessionState(),
    )
    return service, driver


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


def test_cw_configuration_uses_one_write_and_independent_snapshot_readback() -> None:
    request = RfCwRequest(port_id="rf_out", frequency_hz=2_000_000.0)
    service, driver = _cw_service(
        [_snapshot(), _snapshot(frequency_hz=2_000_000.0)]
    )

    result = service.configure_cw(request)

    assert result.port_id == "rf_out"
    assert result.frequency_hz == 2_000_000.0
    assert result.power_dbm is None
    assert driver.requests == [request]
    assert driver.calls == ["snapshot", "configure_cw", "snapshot"]


@pytest.mark.parametrize(
    ("snapshot", "message"),
    (
        (_snapshot(output_enabled=True), "target RF output OFF"),
        (_snapshot(modulation=RfModulationState.ENABLED), "modulation disabled"),
        (_snapshot(pulse=RfPulseState.ENABLED), "Pulse disabled"),
        (_snapshot(sweep=RfSweepState.ENABLED), "Sweep disabled"),
        (_snapshot(protection_codes=("overtemperature",)), "active protection"),
    ),
)
def test_cw_configuration_rejects_unsafe_preflight_without_write(
    snapshot: RfSourceSnapshot,
    message: str,
) -> None:
    service, driver = _cw_service([snapshot])

    with pytest.raises(ConfigError, match=message):
        service.configure_cw(RfCwRequest(port_id="rf_out", power_dbm=-20.0))

    assert driver.requests == []
    assert driver.calls == ["snapshot"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_cw_configuration_checks_capability_access_and_static_profile_before_write() -> None:
    request = RfCwRequest(port_id="rf_out", frequency_hz=2_000_000.0)
    missing_capability = _cw_descriptor("rf_source.idn", "rf_source.snapshot")
    service, driver = _cw_service([_snapshot()], descriptor=missing_capability)

    with pytest.raises(ConfigError, match="rf_source.cw_configure"):
        service.configure_cw(request)
    assert driver.calls == []

    read_only, read_only_driver = _cw_service(
        [_snapshot()],
        access="read_only",
    )
    with pytest.raises(AccessDeniedError, match="rf_source.set_frequency"):
        read_only.configure_cw(request)
    assert read_only_driver.calls == []

    frequency_disabled = _cw_descriptor(
        "rf_source.idn",
        "rf_source.snapshot",
        "rf_source.cw_configure",
        frequency_configurable=False,
    )
    profile_service, profile_driver = _cw_service(
        [_snapshot()],
        descriptor=frequency_disabled,
    )
    with pytest.raises(ConfigError, match="configurable CW frequency"):
        profile_service.configure_cw(request)
    assert profile_driver.calls == []

    range_service, range_driver = _cw_service([_snapshot()])
    with pytest.raises(ConfigError, match="outside the descriptor range"):
        range_service.configure_cw(RfCwRequest(port_id="rf_out", frequency_hz=1.0))
    assert range_driver.calls == []


def test_cw_configuration_mismatch_or_write_failure_is_not_retried() -> None:
    request = RfCwRequest(port_id="rf_out", power_dbm=-10.0)
    mismatch_service, mismatch_driver = _cw_service(
        [_snapshot(), _snapshot(power_dbm=-11.0)]
    )

    with pytest.raises(ConfigError, match="power_dbm readback does not match"):
        mismatch_service.configure_cw(request)

    assert mismatch_driver.requests == [request]
    assert mismatch_driver.calls == ["snapshot", "configure_cw", "snapshot"]
    assert mismatch_service.session_state is not None
    assert mismatch_service.session_state.health is SessionHealth.UNCERTAIN

    failed_service, failed_driver = _cw_service(
        [_snapshot()],
        raise_after_write=True,
    )
    with pytest.raises(ConfigError, match="failed after transmission"):
        failed_service.configure_cw(request)

    assert failed_driver.requests == [request]
    assert failed_driver.calls == ["snapshot", "configure_cw"]
    assert failed_service.session_state is not None
    assert failed_service.session_state.health is SessionHealth.UNCERTAIN


def test_cw_configuration_with_artifact_preserves_pre_and_postcondition_snapshots() -> None:
    request = RfCwRequest(port_id="rf_out", power_dbm=-10.0)
    service, driver = _cw_service([_snapshot(), _snapshot(power_dbm=-10.0)])

    result, artifact = service.configure_cw_with_artifact(request)

    assert result == RfCwResult(port_id="rf_out", power_dbm=-10.0)
    assert driver.requests == [request]
    assert artifact["operation"] == "rf_source.set_power_dbm"
    assert artifact["preflight_snapshot"]["ports"][0]["power_dbm"]["value"] == -30.0
    assert artifact["postcondition_snapshot"]["ports"][0]["power_dbm"]["value"] == -10.0


def test_rf_output_enable_uses_one_write_and_independent_snapshot_readback() -> None:
    request = RfOutputRequest(port_id="rf_out", enabled=True)
    service, driver = _output_service([_snapshot(), _snapshot(output_enabled=True)])

    result, artifact = service.set_output_with_artifact(request)

    assert result == RfOutputResult(port_id="rf_out", enabled=True, write_completed=True)
    assert driver.output_requests == [request]
    assert driver.calls == ["snapshot", "set_rf_output", "snapshot"]
    assert artifact["operation"] == "rf_source.output_enable"
    assert artifact["postcondition_snapshot"]["ports"][0]["output_enabled"]["value"] is True


@pytest.mark.parametrize(
    ("snapshot", "safety_ports", "message"),
    (
        (_snapshot(), (), "complete safety configuration"),
        (_snapshot(), (_output_safety_port(termination_ohm=75.0),), "actual termination"),
        (_snapshot(power_dbm=1.0), None, "RF power within descriptor and safety ranges"),
        (_snapshot(modulation=RfModulationState.ENABLED), None, "modulation disabled"),
        (_snapshot(pulse=RfPulseState.ENABLED), None, "Pulse disabled"),
        (_snapshot(sweep=RfSweepState.ENABLED), None, "Sweep disabled"),
        (_snapshot(protection_codes=("overtemperature",)), None, "blocking protection"),
        (_snapshot(protection_codes=("unknown_code",)), None, "unknown active protection"),
        (
            replace(
                _snapshot(),
                ports=(
                    replace(
                        _snapshot().ports[0],
                        frequency_hz=RfObserved.missing(
                            RfAvailability.UNKNOWN,
                            RfReasonCode.UNKNOWN_STATE,
                        ),
                    ),
                ),
            ),
            None,
            "readable RF frequency",
        ),
        (
            replace(
                _snapshot(),
                protection=RfObserved.missing(
                    RfAvailability.UNKNOWN,
                    RfReasonCode.UNKNOWN_STATE,
                ),
            ),
            None,
            "readable protection",
        ),
    ),
)
def test_rf_output_enable_rejects_unsafe_preflight_without_write(
    snapshot: RfSourceSnapshot,
    safety_ports: tuple[RfPortSafetyConfig, ...] | None,
    message: str,
) -> None:
    service, driver = _output_service([snapshot], safety_ports=safety_ports)

    with pytest.raises(ConfigError, match=message):
        service.set_output(RfOutputRequest(port_id="rf_out", enabled=True))

    assert driver.output_requests == []
    assert driver.calls == ["snapshot"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_rf_output_enable_checks_access_capability_and_static_profile_before_write() -> None:
    request = RfOutputRequest(port_id="rf_out", enabled=True)
    missing_capability = _output_descriptor("rf_source.idn", "rf_source.snapshot")
    service, driver = _output_service([_snapshot()], descriptor=missing_capability)

    with pytest.raises(ConfigError, match="rf_source.output"):
        service.set_output(request)
    assert driver.calls == []

    read_only, read_only_driver = _output_service([_snapshot()], access="read_only")
    with pytest.raises(AccessDeniedError, match="rf_source.output_enable"):
        read_only.set_output(request)
    assert read_only_driver.calls == []

    incomplete_profile = SimpleNamespace(
        driver_id="example.rf1",
        capabilities=("rf_source.idn", "rf_source.snapshot", "rf_source.output"),
        rf_source_extensions=RfSourceDescriptorExtensions(
            contract_version=RF_SOURCE_CONTRACT_VERSION,
            topology=RfSourceTopology(
                (
                    RfOutputPortProfile(
                        port_id="rf_out",
                        frequency_min_hz=9_000.0,
                        frequency_max_hz=3_000_000_000.0,
                        power_min_dbm=-110.0,
                        power_max_dbm=20.0,
                        power_reference_impedance_ohm=50.0,
                    ),
                )
            ),
            features=(
                RfFeatureCapability(
                    feature=RfFeature.OUTPUT,
                    directions=(RfFeatureDirection.READ,),
                    port_ids=("rf_out",),
                    profile=RfOutputProfile(output_readable=True),
                ),
            ),
        ),
    )
    profile_service, profile_driver = _output_service(
        [_snapshot()],
        descriptor=incomplete_profile,
    )
    with pytest.raises(ConfigError, match="readable output profile"):
        profile_service.set_output(request)
    assert profile_driver.calls == []


def test_rf_output_is_idempotent_without_a_write() -> None:
    on_service, on_driver = _output_service([_snapshot(output_enabled=True)])
    on_result = on_service.set_output(RfOutputRequest(port_id="rf_out", enabled=True))

    assert on_result == RfOutputResult(port_id="rf_out", enabled=True, write_completed=False)
    assert on_driver.output_requests == []
    assert on_driver.calls == ["snapshot"]

    off_service, off_driver = _output_service(
        [_snapshot(output_enabled=False)],
        safety_ports=(),
    )
    off_result = off_service.set_output(RfOutputRequest(port_id="rf_out", enabled=False))

    assert off_result == RfOutputResult(port_id="rf_out", enabled=False, write_completed=False)
    assert off_driver.output_requests == []
    assert off_driver.calls == ["snapshot"]


def test_rf_output_disable_ignores_on_only_unknowns_and_confirms_off() -> None:
    before = _snapshot(output_enabled=True)
    before = replace(
        before,
        ports=(
            replace(
                before.ports[0],
                frequency_hz=RfObserved.missing(
                    RfAvailability.UNKNOWN,
                    RfReasonCode.UNKNOWN_STATE,
                ),
                power_dbm=RfObserved.missing(
                    RfAvailability.UNKNOWN,
                    RfReasonCode.UNKNOWN_STATE,
                ),
            ),
        ),
        protection=RfObserved.missing(
            RfAvailability.UNKNOWN,
            RfReasonCode.UNKNOWN_STATE,
        ),
    )
    service, driver = _output_service(
        [before, _snapshot(output_enabled=False)],
        safety_ports=(),
    )

    result = service.set_output(RfOutputRequest(port_id="rf_out", enabled=False))

    assert result == RfOutputResult(port_id="rf_out", enabled=False, write_completed=True)
    assert driver.output_requests == [RfOutputRequest(port_id="rf_out", enabled=False)]
    assert driver.calls == ["snapshot", "set_rf_output", "snapshot"]


def test_rf_output_enable_postcondition_or_write_failure_runs_one_off_recovery() -> None:
    request = RfOutputRequest(port_id="rf_out", enabled=True)
    mismatch_service, mismatch_driver = _output_service(
        [_snapshot(), _snapshot(output_enabled=False), _snapshot(output_enabled=False)]
    )

    with pytest.raises(ConfigError, match="postcondition reports RF output OFF") as mismatch:
        mismatch_service.set_output(request)

    assert mismatch_driver.output_requests == [
        RfOutputRequest(port_id="rf_out", enabled=True),
        RfOutputRequest(port_id="rf_out", enabled=False),
    ]
    assert mismatch.value.rf_source_recovery == {
        "status": "off_verified",
        "session_health": "uncertain",
    }
    assert mismatch_service.session_state is not None
    assert mismatch_service.session_state.health is SessionHealth.UNCERTAIN

    failed_service, failed_driver = _output_service(
        [_snapshot(), _snapshot(output_enabled=False)],
        raise_after_enable=True,
    )
    with pytest.raises(ConfigError, match="RF ON failed after transmission") as failed:
        failed_service.set_output(request)

    assert failed_driver.output_requests == [
        RfOutputRequest(port_id="rf_out", enabled=True),
        RfOutputRequest(port_id="rf_out", enabled=False),
    ]
    assert failed.value.rf_source_recovery["status"] == "off_verified"
    assert failed_service.session_state is not None
    assert failed_service.session_state.health is SessionHealth.UNCERTAIN


def test_rf_output_disable_unknown_result_is_not_retried() -> None:
    request = RfOutputRequest(port_id="rf_out", enabled=False)
    service, driver = _output_service(
        [_snapshot(output_enabled=True)],
        raise_after_disable=True,
    )

    with pytest.raises(ConfigError, match="RF OFF failed after transmission"):
        service.set_output(request)

    assert driver.output_requests == [request]
    assert driver.calls == ["snapshot", "set_rf_output"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.POISONED


def test_rf_output_recovery_uses_bounded_guarded_io_on_uncertain_session() -> None:
    state = InstrumentSessionState()
    driver = GuardedRfOutputDriver(
        [_snapshot(), _snapshot(output_enabled=False), _snapshot(output_enabled=False)],
        state,
    )
    service = RfSourceService(
        config=_config(access="read_write", safety_ports=(_output_safety_port(),)),
        logger=CommandLogger(),
        session=driver,
        descriptor=_output_descriptor(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.output",
        ),
        transport=driver.transport,
        session_state=state,
    )

    with pytest.raises(ConfigError, match="postcondition reports RF output OFF") as raised:
        service.set_output(RfOutputRequest(port_id="rf_out", enabled=True))

    assert driver.output_requests == [
        RfOutputRequest(port_id="rf_out", enabled=True),
        RfOutputRequest(port_id="rf_out", enabled=False),
    ]
    assert driver.inner.writes == ["RF:OUTPUT ON", "RF:OUTPUT OFF"]
    assert driver.transport.counters.write_completed == 2
    assert driver.transport.counters.query_calls == 3
    assert raised.value.rf_source_recovery == {
        "status": "off_verified",
        "session_health": "uncertain",
    }
    assert state.health is SessionHealth.UNCERTAIN
