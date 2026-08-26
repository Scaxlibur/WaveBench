from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
    RF_SOURCE_CONTRACT_VERSION,
    RfFeature,
    RfFeatureCapability,
    RfFeatureDirection,
    RfModulationKind,
    RfModulationModeProfile,
    RfModulationProfile,
    RfModulationRequest,
    RfModulationSnapshot,
    RfModulationSource,
    RfModulationState,
    RfModulationStateSnapshot,
    RfModulationValueUnit,
    RfModulationWaveform,
    RfObserved,
    RfOutputPortProfile,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseState,
    RfSourceDescriptorExtensions,
    RfSourceSnapshot,
    RfSourceTopology,
    RfSweepState,
)
from wavebench.logging import CommandLogger
from wavebench.services.rf_source_service import RfSourceService
from wavebench.transport.session import InstrumentSessionState, SessionHealth


def _config(*, access: str = "read_write") -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "dmax"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("test.toml"),
        rf_source=RfSourceConfig(
            driver="example.rf.modulation",
            resource="TCPIP::rf::INSTR",
            access=access,  # type: ignore[arg-type]
        ),
    )


def _profile() -> RfModulationProfile:
    return RfModulationProfile(
        state_readable=True,
        configuration_readable=True,
        mode_profiles=(
            RfModulationModeProfile(
                kind=RfModulationKind.AM,
                value_unit=RfModulationValueUnit.PERCENT,
                value_min=0.0,
                value_max=100.0,
                internal_frequency_min_hz=10.0,
                internal_frequency_max_hz=100_000.0,
            ),
            RfModulationModeProfile(
                kind=RfModulationKind.FM,
                value_unit=RfModulationValueUnit.HZ,
                value_min=0.1,
                value_max=1_000_000.0,
                internal_frequency_min_hz=10.0,
                internal_frequency_max_hz=100_000.0,
            ),
            RfModulationModeProfile(
                kind=RfModulationKind.PM,
                value_unit=RfModulationValueUnit.RAD,
                value_min=0.0,
                value_max=5.0,
                internal_frequency_min_hz=10.0,
                internal_frequency_max_hz=100_000.0,
            ),
        ),
    )


def _descriptor(*capabilities: str, profile: RfModulationProfile | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.modulation",
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
                    feature=RfFeature.MODULATION,
                    directions=(RfFeatureDirection.CONFIGURE, RfFeatureDirection.READ),
                    port_ids=("rf_out",),
                    profile=profile or _profile(),
                ),
            ),
        ),
    )


def _rf_snapshot(
    *,
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
                frequency_hz=RfObserved.value_of(1_000_000.0),
                power_dbm=RfObserved.value_of(-50.0),
                output_enabled=RfObserved.value_of(output_enabled),
                modulation=RfObserved.value_of(modulation),
                pulse=RfObserved.value_of(pulse),
                sweep=RfObserved.value_of(sweep),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=protection_codes)),
    )


def _modulation_snapshot(
    *,
    kind: RfModulationKind = RfModulationKind.AM,
    enabled: bool = False,
    value: float = 50.0,
    source: RfModulationSource = RfModulationSource.INTERNAL,
    waveform: RfModulationWaveform = RfModulationWaveform.SINE,
    selected_fm_pm_kind: RfModulationKind | None = None,
    faults: tuple[str, ...] = (),
) -> RfModulationSnapshot:
    fields: dict[str, object] = {
        "port_id": "rf_out",
        "kind": kind,
        "source": source,
        "waveform": waveform,
        "internal_frequency_hz": 1_000.0,
        "selected_fm_pm_kind": (
            selected_fm_pm_kind
            if selected_fm_pm_kind is not None
            else (kind if kind in {RfModulationKind.FM, RfModulationKind.PM} else None)
        ),
        "enabled_modes": (kind,) if enabled else (),
        "global_enabled": enabled,
        "fault_codes": faults,
    }
    if kind is RfModulationKind.AM:
        fields["depth_percent"] = value
    elif kind is RfModulationKind.FM:
        fields["frequency_deviation_hz"] = value
    else:
        fields["phase_deviation_rad"] = value
    return RfModulationSnapshot(**fields)  # type: ignore[arg-type]


class _Driver:
    def __init__(
        self,
        rf_snapshots: list[RfSourceSnapshot],
        modulation_snapshots: list[RfModulationSnapshot],
    ) -> None:
        self.rf_snapshots = list(rf_snapshots)
        self.modulation_snapshots = list(modulation_snapshots)
        self.calls: list[str] = []
        self.requests: list[RfModulationRequest] = []

    def close(self) -> None:
        self.calls.append("close")

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        self.calls.append("snapshot")
        if not self.rf_snapshots:
            raise AssertionError("unexpected RF snapshot")
        return self.rf_snapshots.pop(0)

    def get_rf_modulation_snapshot(
        self,
        port_id: str,
        kind: RfModulationKind,
    ) -> RfModulationSnapshot:
        self.calls.append("modulation_snapshot")
        assert port_id == "rf_out"
        if not self.modulation_snapshots:
            raise AssertionError("unexpected modulation snapshot")
        snapshot = self.modulation_snapshots.pop(0)
        assert snapshot.kind is kind
        return snapshot

    def get_rf_modulation_state(self, port_id: str) -> RfModulationStateSnapshot:
        self.calls.append("modulation_state")
        assert port_id == "rf_out"
        if not self.modulation_snapshots:
            raise AssertionError("unexpected modulation state")
        snapshot = self.modulation_snapshots.pop(0)
        return RfModulationStateSnapshot(
            port_id=snapshot.port_id,
            enabled_modes=snapshot.enabled_modes,
            global_enabled=snapshot.global_enabled,
            fault_codes=snapshot.fault_codes,
        )

    def configure_rf_modulation(self, request: RfModulationRequest) -> None:
        self.calls.append("configure_modulation")
        self.requests.append(request)


def _service(
    rf_snapshots: list[RfSourceSnapshot],
    modulation_snapshots: list[RfModulationSnapshot],
    *,
    access: str = "read_write",
    descriptor: SimpleNamespace | None = None,
) -> tuple[RfSourceService, _Driver]:
    driver = _Driver(rf_snapshots, modulation_snapshots)
    service = RfSourceService(
        config=_config(access=access),
        logger=CommandLogger(),
        session=driver,
        descriptor=descriptor
        or _descriptor(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.modulation_configure",
        ),
        session_state=InstrumentSessionState(),
    )
    return service, driver


def _am_request(*, depth_percent: float = 50.0) -> RfModulationRequest:
    return RfModulationRequest(
        port_id="rf_out",
        kind=RfModulationKind.AM,
        internal_frequency_hz=1_000.0,
        depth_percent=depth_percent,
    )


def test_modulation_uses_one_driver_sequence_and_independent_readback() -> None:
    request = _am_request()
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot(modulation=RfModulationState.ENABLED)],
        [_modulation_snapshot(), _modulation_snapshot(enabled=True)],
    )

    result, artifact = service.configure_modulation_with_artifact(request)

    assert result.kind is RfModulationKind.AM
    assert result.depth_percent == 50.0
    assert driver.requests == [request]
    assert driver.calls == [
        "snapshot",
        "modulation_state",
        "configure_modulation",
        "snapshot",
        "modulation_snapshot",
    ]
    assert artifact["operation"] == "rf_source.modulation_configure"
    assert artifact["postcondition_modulation_snapshot"]["global_enabled"] is True
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_modulation_replaces_an_inactive_external_square_profile_with_internal_sine() -> None:
    request = _am_request()
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot(modulation=RfModulationState.ENABLED)],
        [
            _modulation_snapshot(
                source=RfModulationSource.EXTERNAL,
                waveform=RfModulationWaveform.SQUARE,
            ),
            _modulation_snapshot(enabled=True),
        ],
    )

    result = service.configure_modulation(request)

    assert result.depth_percent == request.depth_percent
    assert driver.requests == [request]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_modulation_rejects_a_non_internal_sine_postcondition() -> None:
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot(modulation=RfModulationState.ENABLED)],
        [
            _modulation_snapshot(),
            _modulation_snapshot(
                enabled=True,
                source=RfModulationSource.EXTERNAL,
                waveform=RfModulationWaveform.SQUARE,
            ),
        ],
    )

    with pytest.raises(ConfigError, match="postcondition requires the requested internal-sine"):
        service.configure_modulation(_am_request())

    assert len(driver.requests) == 1
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN


def test_modulation_allows_off_only_fm_pm_selection_change_before_fixed_write() -> None:
    request = RfModulationRequest(
        port_id="rf_out",
        kind=RfModulationKind.PM,
        internal_frequency_hz=1_000.0,
        phase_deviation_rad=2.0,
    )
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot(modulation=RfModulationState.ENABLED)],
        [
            _modulation_snapshot(
                kind=RfModulationKind.PM,
                value=2.0,
                selected_fm_pm_kind=RfModulationKind.FM,
            ),
            _modulation_snapshot(
                kind=RfModulationKind.PM,
                enabled=True,
                value=2.0,
                selected_fm_pm_kind=RfModulationKind.PM,
            ),
        ],
    )

    result = service.configure_modulation(request)

    assert result.kind is RfModulationKind.PM
    assert driver.requests == [request]
    assert driver.calls == [
        "snapshot",
        "modulation_state",
        "configure_modulation",
        "snapshot",
        "modulation_snapshot",
    ]


def test_modulation_requires_fm_pm_selection_after_fixed_write() -> None:
    request = RfModulationRequest(
        port_id="rf_out",
        kind=RfModulationKind.PM,
        internal_frequency_hz=1_000.0,
        phase_deviation_rad=2.0,
    )
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot(modulation=RfModulationState.ENABLED)],
        [
            _modulation_snapshot(
                kind=RfModulationKind.PM,
                value=2.0,
                selected_fm_pm_kind=RfModulationKind.FM,
            ),
            _modulation_snapshot(
                kind=RfModulationKind.PM,
                enabled=True,
                value=2.0,
                selected_fm_pm_kind=RfModulationKind.FM,
            ),
        ],
    )

    with pytest.raises(ConfigError, match="does not select the requested FM/PM kind"):
        service.configure_modulation(request)

    assert driver.requests == [request]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN


@pytest.mark.parametrize(
    ("rf_snapshot", "modulation_snapshot", "message"),
    (
        (_rf_snapshot(output_enabled=True), _modulation_snapshot(), "target RF output OFF"),
        (
            _rf_snapshot(pulse=RfPulseState.ENABLED),
            _modulation_snapshot(),
            "Pulse disabled",
        ),
        (
            _rf_snapshot(sweep=RfSweepState.ENABLED),
            _modulation_snapshot(),
            "Sweep disabled",
        ),
        (
            _rf_snapshot(protection_codes=("overtemperature",)),
            _modulation_snapshot(),
            "active protection",
        ),
        (_rf_snapshot(), _modulation_snapshot(enabled=True), "all modulation modes disabled"),
        (
            _rf_snapshot(),
            _modulation_snapshot(faults=("am_overmodulation",)),
            "modulation fault",
        ),
    ),
)
def test_modulation_rejects_unsafe_preflight_without_write(
    rf_snapshot: RfSourceSnapshot,
    modulation_snapshot: RfModulationSnapshot,
    message: str,
) -> None:
    service, driver = _service([rf_snapshot], [modulation_snapshot])

    with pytest.raises(ConfigError, match=message):
        service.configure_modulation(_am_request())

    assert driver.requests == []
    assert driver.calls == ["snapshot", "modulation_state"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_modulation_checks_capability_access_and_descriptor_range_before_driver_io() -> None:
    missing, missing_driver = _service(
        [],
        [],
        descriptor=_descriptor("rf_source.idn", "rf_source.snapshot"),
    )
    with pytest.raises(ConfigError, match="rf_source.modulation_configure"):
        missing.configure_modulation(_am_request())
    assert missing_driver.calls == []

    read_only, read_only_driver = _service([], [], access="read_only")
    with pytest.raises(AccessDeniedError, match="rf_source.modulation_configure"):
        read_only.configure_modulation(_am_request())
    assert read_only_driver.calls == []

    range_service, range_driver = _service([], [])
    with pytest.raises(ConfigError, match="outside the descriptor range"):
        range_service.configure_modulation(_am_request(depth_percent=101.0))
    assert range_driver.calls == []


def test_modulation_mismatch_is_not_retried_and_degrades_session() -> None:
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot(modulation=RfModulationState.ENABLED)],
        [_modulation_snapshot(), _modulation_snapshot(enabled=True, value=49.0)],
    )

    with pytest.raises(ConfigError, match="readback does not match"):
        service.configure_modulation(_am_request())

    assert len(driver.requests) == 1
    assert driver.calls == [
        "snapshot",
        "modulation_state",
        "configure_modulation",
        "snapshot",
        "modulation_snapshot",
    ]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN
