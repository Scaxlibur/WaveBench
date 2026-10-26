"""Offline observation contracts using real factory, guard, epochs and leases."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from wavebench.config import load_config
from wavebench.errors import (
    ConfigError, ConnectionError, DataError, ResourceBusyError,
    SessionCloseError, SessionHealthError, TransportIOError,
)
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.builtin import BUILTIN_INSTRUMENTS
from wavebench.instruments.models import (
    SCOPE_SNAPSHOT_V2_FIELD_ORDER, ScopeAnalogChannelSnapshotV2,
    ScopeChannelInputStateV2, ScopeIdentitySnapshot, ScopeSnapshotV2,
    WaveformData, WaveformHeader,
)
from wavebench.instruments.registry import InstrumentRegistry
from wavebench.instruments.scope_extensions import (
    ScopeDescriptorExtensions, ScopeSnapshotProfileV2,
    ScopeWaveformBinaryOperationProfile, ScopeWaveformBinaryProfile,
)
from wavebench.logging import CommandLogger
from wavebench.services.agent_observe import scope_observe_payload, scope_waveform_report_payload
from wavebench.services.resource_lease import ResourceLease
from wavebench.services.scope_service import ScopeService
from wavebench.transport.contracts import (
    CommandTransmission, ReplayPolicy, ResponseProgress, Synchronization, TransportPhase,
)
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import SessionHealth


RESOURCE = "TCPIP::offline-scope-contract.invalid::INSTR"
FIELDS = (
    "identity.manufacturer", "identity.model", "identity.serial_number",
    "identity.firmware", "identity.options", "channel.channel", "channel.coupling",
)


class AnalysisFailureWaveform(WaveformData):
    def summary(self, *args, **kwargs):
        raise DataError("deterministic waveform analysis failed")


class ConcreteTransport:
    resource = RESOURCE

    def __init__(self, harness):
        self.harness = harness
        self.events = []

    def query(self, command, *, replay=ReplayPolicy.NO_REPLAY):
        assert replay is ReplayPolicy.NO_REPLAY
        self.events.append(("query", command))
        if self.harness.query_failure:
            raise OSError("connection lost during safety query")
        if command == "*IDN?":
            return "RIGOL TECHNOLOGIES,DS1104Z Plus,OFFLINE,1.0"
        if "COUPLING?" in command:
            channel = int(command.rsplit("CH", 1)[1])
            self.harness.input_reads.append(channel)
            if len(self.harness.input_reads) == self.harness.input_failure_at:
                raise OSError("connection lost during current input check")
            if self.harness.compete:
                self.harness.check_competitor()
            if self.harness.change_input and len(self.harness.input_reads) >= 3:
                return "DC"
            return "DCL"
        if command.endswith(":COUPling?"):
            return "DC"
        if command.startswith("STATUS?"):
            return "DCL"
        if command == ":WAVeform:PREamble?":
            return "0,0,1000,1,0.00001,0,0,0.01,128,0"
        if command == ":SYSTem:ERRor?":
            return '0,"No error"'
        raise AssertionError(f"unexpected query: {command}")

    def write(self, command):
        self.events.append(("write", command))
        if command == "FETCH CH1" and self.harness.failure in {"uncertain", "poisoned", "oserror"}:
            if self.harness.failure == "oserror":
                raise OSError("connection lost after dispatch")
            raise TransportIOError(
                "waveform write result unknown", operation="write", phase=TransportPhase.SENDING,
                replay_policy=ReplayPolicy.NO_REPLAY, command_transmission=CommandTransmission.UNKNOWN,
                response_progress=ResponseProgress.NONE, attempts=1,
                synchronization=(Synchronization.PROVEN if self.harness.failure == "uncertain"
                                 else Synchronization.LOST),
            )

    def query_bin_block(self, command, *, replay=ReplayPolicy.NO_REPLAY):
        self.events.append(("binary_query", command))
        return np.full(1000, 128, dtype=np.uint8).tobytes()

    def record_event(self, direction, text):
        pass

    def close(self):
        self.events.append(("close", ""))
        if self.harness.backend_close_failure:
            raise OSError("backend close failed")


class QueryScope:
    def __init__(self, harness, transport):
        self.harness = harness
        self.transport = transport

    def idn(self):
        return self.transport.query("*IDN?")

    def channel_coupling(self, channel):
        if channel not in {1, 2}:
            raise DataError("channel outside fake scope range")
        if self.harness.mutating_input:
            self.transport.write("FORBIDDEN INPUT WRITE")
        return self.transport.query(f"COUPLING? CH{channel}")

    def get_channel_input_state_v2(self, channel):
        self.channel_coupling(channel)
        return ScopeChannelInputStateV2(channel, "dc", "unknown", unavailable_fields=("impedance_ohm",))

    def get_snapshot(self, channel):
        # Legacy snapshots can consume state through text queries. Observation
        # must use the descriptor's V2 contract instead of this method.
        self.transport.query(":SYSTem:ERRor?")
        raise AssertionError("legacy snapshot must not run")

    def get_snapshot_v2(self, channel, *, fields):
        assert fields == FIELDS
        if self.harness.mutating_snapshot:
            self.transport.write("FORBIDDEN SNAPSHOT WRITE")
        self.idn()
        coupling = self.transport.query(f"STATUS? CH{channel}")
        return ScopeSnapshotV2(
            identity=ScopeIdentitySnapshot("RIGOL", "DS1104Z Plus", "OFFLINE", "1.0", ()),
            channel=ScopeAnalogChannelSnapshotV2(channel, coupling=coupling),
            unavailable_fields=tuple(field for field in SCOPE_SNAPSHOT_V2_FIELD_ORDER if field not in FIELDS),
        )

    def fetch_waveform(self, *, channel, points, check_errors):
        assert points.upper() == "DEF" and not check_errors
        self.harness.fetch_health.append(self.transport.session_state.health)
        if channel == 1:
            if self.harness.failure == "session_health":
                raise SessionHealthError(
                    "session unavailable", health="uncertain", io_kind="write",
                    epoch_id=self.transport.session_state.epoch_id,
                )
            if self.harness.failure == "connection":
                raise ConnectionError("connection lost outside transport")
            if self.harness.failure == "config":
                raise ConfigError("fetch configuration invalid")
            if self.harness.failure == "data":
                raise DataError("malformed waveform after completed query")
        self.transport.write(f"FETCH CH{channel}")
        if channel == 1 and self.harness.failure == "returned_poisoned":
            self.transport.session_state.degrade(SessionHealth.POISONED, reason="fake_lost_sync")
        waveform_type = AnalysisFailureWaveform if channel == 1 and self.harness.failure == "analysis" else WaveformData
        return waveform_type(
            channel, WaveformHeader(0.0, 0.01, 1000),
            np.sin(np.linspace(0.0, 20 * np.pi, 1000)),
        )

    def close(self):
        if self.harness.driver_close_failure:
            raise OSError("plugin close failed before delegation")
        self.transport.close()


class Harness:
    def __init__(self, root: Path, monkeypatch):
        self.root = root
        self.monkeypatch = monkeypatch
        self.transports = []
        self.guards = []
        self.constructor_calls = 0
        self.constructor_io = None
        self.input_reads = []
        self.fetch_health = []
        self.failure = None
        self.query_failure = False
        self.input_failure_at = None
        self.mutating_input = False
        self.mutating_snapshot = False
        self.change_input = False
        self.compete = False
        self.driver_close_failure = False
        self.backend_close_failure = False
        self.config_path = root / "scope.toml"
        monkeypatch.setenv("WAVEBENCH_LEASE_DIR", str(root / "leases"))
        monkeypatch.setattr("wavebench.instruments.factory._open_transport", self.open_transport)
        self.install()

    def open_transport(self, **kwargs):
        assert kwargs["resource"] == RESOURCE
        transport = ConcreteTransport(self)
        self.transports.append(transport)
        return transport

    def factory(self, context):
        self.constructor_calls += 1
        transport = context.open_transport()
        assert isinstance(transport, GuardedAuditedTransport)
        self.guards.append(transport)
        if self.constructor_io == "write":
            transport.write(":STOP")
        elif self.constructor_io == "query":
            transport.query("*IDN?")
        elif self.constructor_io == "binary_query":
            transport.query_bin_block(":WAVeform:DATA?")
        return QueryScope(self, transport)

    def install(self, *, access="read_write", builtin=False, capabilities=None):
        if builtin:
            descriptor = next(item for item in BUILTIN_INSTRUMENTS if item.driver_id == "rigol.ds1104")
            original_factory = descriptor.factory

            def factory(context):
                driver = original_factory(context)
                self.guards.append(driver.transport)
                return driver

            descriptor = replace(descriptor, factory=factory)
        else:
            descriptor = InstrumentDescriptor(
                driver_id="test.observation-scope", kind="scope", display_name="Offline scope",
                manufacturer="RIGOL", models=("DS1104Z Plus",), aliases=(),
                capabilities=capabilities or (
                    "scope.idn", "scope.snapshot", "scope.snapshot_v2",
                    "scope.channel_coupling", "scope.fetch_waveform",
                ),
                idn_patterns=("RIGOL",), backends=("pyvisa",), option_specs=(),
                permissions=("instrument.io",), factory=self.factory,
                scope_coupling_policy="switchable-termination", wavebench_min_version="0.8.24",
                scope_extensions=ScopeDescriptorExtensions(
                    snapshot_profile_v2=ScopeSnapshotProfileV2(FIELDS, max_queries=2),
                ) if capabilities is None or "scope.snapshot_v2" in capabilities else None,
            )
        self.descriptor = descriptor
        registry = InstrumentRegistry(builtins=(descriptor,))
        self.monkeypatch.setattr("wavebench.instruments.registry.build_instrument_registry", lambda **kwargs: registry)
        self.config_path.write_text(
            f'[connection]\nresource = "{RESOURCE}"\nbackend = "pyvisa"\n'
            f'[scope]\ndriver = "{descriptor.driver_id}"\naccess = "{access}"\ncheck_errors = false\n'
            '[waveform]\npoints = "def"\n', encoding="utf-8",
        )

    @property
    def writes(self):
        return [command for transport in self.transports for kind, command in transport.events if kind == "write"]

    def report(self, channels=(1, 2)):
        return scope_waveform_report_payload(config_path=self.config_path, channels=channels)

    def service(self):
        return ScopeService(load_config(self.config_path), CommandLogger())

    def check_competitor(self):
        # A separate process exercises the actual OS lock rather than an
        # in-process mock. It tries to take the lease while a safety query runs.
        result = subprocess.run(
            [sys.executable, "-c", (
                "from wavebench.services.resource_lease import ResourceLease\n"
                "from wavebench.errors import ResourceBusyError\n"
                f"lease = ResourceLease({RESOURCE!r})\n"
                "try:\n    lease.acquire()\n"
                "except ResourceBusyError:\n    print('busy')\n"
                "else:\n    lease.release()\n    print('acquired')\n"
            )], capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "busy"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def test_report_retains_one_epoch_and_lease_through_all_preflight_and_fetch(harness):
    harness.compete = True
    payload = harness.report()
    assert payload["status"] == "ok"
    assert len(harness.guards) == len(harness.transports) == 1
    events = harness.transports[0].events
    assert events.index(("query", "COUPLING? CH2")) < events.index(("write", "FETCH CH1"))
    assert harness.input_reads == [1, 2, 1, 1, 2, 2]
    assert harness.fetch_health == [SessionHealth.HEALTHY, SessionHealth.HEALTHY]
    assert harness.guards[0].session_state.health is SessionHealth.CLOSED
    assert harness.guards[0].lease.acquired is False
    assert payload["waveform_source"]["same_acquisition"] is False
    assert payload["relationships"][0]["correlation"]["status"] == "skipped"


def test_builtin_ds1104_invalid_later_channel_has_zero_writes(harness):
    harness.install(builtin=True)
    payload = harness.report((1, 5))
    assert payload["status"] == "partial"
    assert [item["waveform"]["status"] for item in payload["channels"]] == ["skipped", "skipped"]
    assert harness.writes == []
    assert len(harness.guards) == 1


def test_query_only_preflight_blocks_a_plugin_input_write(harness):
    harness.mutating_input = True
    payload = harness.report()
    assert harness.guards[0].access == "read_write"
    assert harness.writes == []
    assert all(item["waveform"]["status"] == "skipped" for item in payload["channels"])
    assert any("SessionHealthError" in warning for warning in payload["warnings"])


@pytest.mark.parametrize("access", ["read_write", "read_only", "disabled"])
def test_readonly_observation_tightens_access_and_uses_v2_query_phase(harness, access):
    harness.install(access=access)
    payload = scope_observe_payload(config_path=harness.config_path, channel=1)
    assert harness.writes == []
    assert payload["read_only"] and payload["query_only"]
    if access == "disabled":
        assert harness.guards == harness.transports == []
        assert harness.constructor_calls == 0
        assert payload["scope_status"]["status"] == "skipped"
        return
    assert harness.guards[0].access == "read_only"
    assert payload["scope_status"]["status"] == "ok"
    assert not any(command == ":SYSTem:ERRor?" for kind, command in harness.transports[0].events)


def test_readonly_snapshot_plugin_write_is_blocked(harness):
    harness.mutating_snapshot = True
    payload = scope_observe_payload(config_path=harness.config_path)
    assert payload["scope_status"]["status"] == "unavailable"
    assert harness.writes == []
    assert harness.guards[0].counters.blocked_write_requests == 1


def test_legacy_snapshot_is_not_used_without_a_pure_query_field_contract(harness):
    harness.install(capabilities=("scope.idn", "scope.snapshot", "scope.channel_coupling", "scope.fetch_waveform"))
    payload = scope_observe_payload(config_path=harness.config_path)
    assert payload["status"] == "partial"
    assert payload["scope_status"]["status"] == "unavailable"
    assert payload["identity"]["status"] == payload["coupling"]["status"] == "ok"
    assert harness.writes == []


@pytest.mark.parametrize("failure", [
    "uncertain", "poisoned", "oserror", "session_health", "connection", "returned_poisoned", "config",
])
def test_report_stops_after_uncertain_io_or_fetch_config_error_without_reopen(harness, failure):
    harness.failure = failure
    payload = harness.report()
    assert payload["status"] == "partial"
    assert payload["channels"][1]["waveform"]["status"] == "skipped"
    assert payload["channels"][1]["waveform"]["reason"]
    if failure != "returned_poisoned":
        expected_type = {
            "uncertain": "TransportIOError", "poisoned": "TransportIOError", "oserror": "OSError",
            "session_health": "SessionHealthError", "connection": "ConnectionError", "config": "ConfigError",
        }[failure]
        assert payload["channels"][0]["waveform"]["error"]["type"] == expected_type
    assert "FETCH CH2" not in harness.writes
    assert len(harness.guards) == 1


def test_definitive_data_failure_can_continue_on_the_same_healthy_epoch(harness):
    harness.failure = "data"
    payload = harness.report()
    assert payload["channels"][0]["waveform"]["status"] == "unavailable"
    assert payload["channels"][1]["waveform"]["status"] == "ok"
    assert harness.writes == ["FETCH CH2"]
    assert len(harness.guards) == 1


def test_deterministic_analysis_failure_preserves_later_channel_and_expectations(harness):
    harness.failure = "analysis"
    payload = scope_waveform_report_payload(
        config_path=harness.config_path, channels=(1, 2),
        expectations={1: {"vpp_v": 2.0}, 2: {"vpp_v": 2.0}},
    )
    assert payload["status"] == "partial"
    assert payload["channels"][0]["waveform"]["error"]["type"] == "DataError"
    assert payload["channels"][1]["waveform"]["status"] == "ok"
    assert payload["expectations"]["channels"] == {"1": "unavailable", "2": "pass"}
    assert harness.writes == ["FETCH CH1", "FETCH CH2"]
    assert len(harness.guards) == 1


def test_later_input_uncertainty_preserves_first_waveform_and_stops_mutation(harness):
    harness.input_failure_at = 5
    payload = harness.report()
    assert payload["channels"][0]["waveform"]["status"] == "ok"
    assert payload["channels"][1]["coupling"]["error"]["type"] == "OSError"
    assert payload["channels"][1]["waveform"]["status"] == "skipped"
    assert harness.writes == ["FETCH CH1"]
    assert len(harness.guards) == 1


def test_current_input_is_rechecked_before_fetch(harness):
    harness.change_input = True
    payload = harness.report((1,))
    assert harness.input_reads == [1, 1, 1]
    assert payload["channels"][0]["waveform"]["status"] == "unavailable"
    assert "50 ohm" in payload["channels"][0]["waveform"]["error"]["message"]
    assert harness.writes == []


def test_unknown_input_termination_is_rejected_even_with_allow_50ohm(harness):
    harness.install(capabilities=("scope.idn", "scope.channel_input_state_v2", "scope.fetch_waveform"))
    payload = scope_waveform_report_payload(config_path=harness.config_path, allow_50ohm=True)
    assert payload["channels"][0]["waveform"]["status"] == "skipped"
    assert harness.writes == []


@pytest.mark.parametrize("access", ["read_only", "disabled"])
def test_mutating_report_does_not_enlarge_existing_access(harness, access):
    harness.install(access=access)
    payload = harness.report()
    assert harness.guards == harness.transports == []
    assert harness.constructor_calls == 0
    assert harness.writes == []
    assert all(item["waveform"]["status"] == "skipped" for item in payload["channels"])


@pytest.mark.parametrize("missing", ["scope.idn", "scope.fetch_waveform", "scope.channel_coupling"])
def test_missing_required_capability_hard_blocks_mutation(harness, missing):
    harness.install(capabilities=tuple(item for item in (
        "scope.idn", "scope.fetch_waveform", "scope.channel_coupling",
    ) if item != missing))
    payload = harness.report()
    assert harness.writes == []
    assert harness.guards == harness.transports == []
    assert harness.constructor_calls == 0
    assert all(item["waveform"]["status"] == "skipped" for item in payload["channels"])


@pytest.mark.parametrize("setting", ['format = "ascii"', 'byte_order = "msbf"'])
def test_offline_waveform_settings_block_mutation_before_fetch(harness, setting):
    harness.constructor_io = "write"
    with harness.config_path.open("a", encoding="utf-8") as file:
        file.write(setting + "\n")
    payload = harness.report()
    assert harness.writes == []
    assert harness.guards == harness.transports == []
    assert harness.constructor_calls == 0
    assert all(item["waveform"]["status"] == "skipped" for item in payload["channels"])


def test_direct_offline_fetch_validation_never_runs_factory(harness):
    harness.constructor_io = "write"
    with harness.config_path.open("a", encoding="utf-8") as file:
        file.write('format = "ascii"\n')
    with pytest.raises(ConfigError, match="waveform.format"):
        harness.service().validate_observation_fetch()
    assert harness.constructor_calls == 0
    assert harness.transports == harness.guards == []


def test_invalid_bounded_profile_never_runs_factory(harness):
    harness.install(capabilities=(
        "scope.idn", "scope.fetch_waveform", "scope.capture_waveform", "scope.channel_coupling",
    ))
    service = harness.service()
    harness.constructor_io = "write"
    # The profile omits capture_single although the capability declares it.
    # Registry/static profile validation must fail before invoking its factory.
    descriptor = replace(harness.descriptor, scope_extensions=ScopeDescriptorExtensions(
        waveform_binary_profile=ScopeWaveformBinaryProfile(operations=(
            ScopeWaveformBinaryOperationProfile(
                operation_kind="fetch", response_max_bytes=1024,
                operation_max_bytes=4096, query_max_count=4, resynchronization_max_bytes=0,
                restore_order=("scope.waveform_source",), snapshot_max_steps=1,
                restore_max_steps=1, verify_max_steps=1,
            ),
        )),
    ))
    registry = InstrumentRegistry(builtins=(descriptor,))
    harness.monkeypatch.setattr("wavebench.instruments.registry.build_instrument_registry", lambda **kwargs: registry)
    service.descriptor = descriptor
    with pytest.raises(ConfigError, match="match declared standard waveform capabilities"):
        service.validate_observation_fetch()
    with pytest.raises(ConfigError, match="match declared standard waveform capabilities"):
        harness.report()
    assert harness.constructor_calls == 0
    assert harness.transports == harness.guards == []


def test_invalid_points_fail_before_any_factory_io(harness):
    text = harness.config_path.read_text(encoding="utf-8").replace('points = "def"', 'points = "invalid"')
    harness.config_path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="waveform points"):
        harness.report()
    assert not harness.transports


@pytest.mark.parametrize("body_failure", [False, True])
def test_borrowed_context_keeps_driver_state_and_lease_alive(harness, body_failure):
    bootstrap = harness.service()
    driver = bootstrap.open_session()
    service = ScopeService(
        bootstrap.config, bootstrap.logger, session=driver, descriptor=bootstrap.descriptor,
        transport=bootstrap.transport, session_state=bootstrap.session_state, lease=bootstrap.lease,
    )
    try:
        def use_borrowed():
            with service.session_context(observation=True):
                service.preflight_observation_fetch((1, 2))
                with service.session_context():
                    service.fetch_waveform(1)
                if body_failure:
                    raise DataError("borrowed body failed")

        if body_failure:
            with pytest.raises(DataError, match="borrowed body failed"):
                use_borrowed()
        else:
            use_borrowed()
        assert service.session is driver
        assert service.session_state.health is SessionHealth.HEALTHY
        assert service.lease.acquired
        with pytest.raises(ResourceBusyError):
            ResourceLease(RESOURCE).acquire()
    finally:
        driver.close()


@pytest.mark.parametrize("body_failure", [False, True])
def test_owned_context_cleans_up_plugin_and_backend_close_failures(harness, body_failure):
    harness.driver_close_failure = harness.backend_close_failure = True
    service = harness.service()
    original = DataError("original body failure")
    with pytest.raises(DataError if body_failure else SessionCloseError) as caught:
        with service.session_context():
            if body_failure:
                raise original
    if body_failure:
        assert caught.value is original
    assert service.session is None
    assert service.session_state.health is SessionHealth.CLOSED
    assert not service.lease.acquired
    lease = ResourceLease(RESOURCE).acquire()
    lease.release()


def test_query_failure_stops_preflight_and_all_waveforms(harness):
    harness.query_failure = True
    payload = harness.report()
    assert payload["identity"]["error"]["type"] == "OSError"
    assert all(item["waveform"]["status"] == "skipped" for item in payload["channels"])
    assert harness.writes == []
    assert len(harness.guards) == 1


def test_report_preserves_completed_sections_when_close_fails(harness):
    harness.driver_close_failure = harness.backend_close_failure = True
    payload = harness.report()
    assert payload["status"] == "partial"
    assert all(item["waveform"]["status"] == "ok" for item in payload["channels"])
    assert payload["session"]["error"]["type"] == "SessionCloseError"
    assert harness.guards[0].session_state.health is SessionHealth.CLOSED
    assert not harness.guards[0].lease.acquired
    assert len(harness.guards) == 1


def test_readonly_missing_identity_never_executes_factory(harness):
    harness.install(capabilities=("scope.channel_coupling",))
    payload = scope_observe_payload(config_path=harness.config_path, channels=(1, 2))
    assert harness.constructor_calls == 0
    assert harness.transports == harness.guards == []
    assert payload["status"] == "partial"
    assert all(item["scope_status"]["status"] == "skipped" for item in payload["channels"])


@pytest.mark.parametrize("constructor_io", ["write", "query", "binary_query"])
@pytest.mark.parametrize("channels", [(1, 2), (1, 5)])
def test_observation_legacy_factory_io_is_latched_before_preflight(harness, constructor_io, channels):
    harness.install(capabilities=("scope.idn", "scope.channel_coupling", "scope.fetch_waveform"))
    harness.constructor_io = constructor_io
    payload = harness.report(channels)
    assert harness.constructor_calls == 1
    assert harness.transports[0].events == [("close", "")]
    assert harness.writes == []
    assert harness.guards[0].session_state.health is SessionHealth.CLOSED
    assert not harness.guards[0].lease.acquired
    assert all(item["waveform"]["status"] == "skipped" for item in payload["channels"])
    assert payload["session"]["error"]["type"] == "ConfigError"
    lease = ResourceLease(RESOURCE).acquire()
    lease.release()


def test_observation_passive_legacy_factory_is_released_after_validation(harness):
    harness.install(capabilities=("scope.idn", "scope.channel_coupling", "scope.fetch_waveform"))
    payload = harness.report()
    assert harness.constructor_calls == 1
    assert harness.guards[0].construction_latched is False
    assert all(item["waveform"]["status"] == "ok" for item in payload["channels"])
    assert harness.writes == ["FETCH CH1", "FETCH CH2"]


def test_ordinary_legacy_open_keeps_existing_factory_io_behavior(harness):
    harness.install(capabilities=("scope.idn", "scope.channel_coupling", "scope.fetch_waveform"))
    harness.constructor_io = "write"
    service = harness.service()
    driver = service.open_session()
    try:
        assert harness.writes == [":STOP"]
    finally:
        driver.close()
