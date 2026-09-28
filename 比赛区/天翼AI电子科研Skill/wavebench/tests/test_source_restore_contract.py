from dataclasses import replace
from types import SimpleNamespace

import pytest

from wavebench.errors import ConfigError
from wavebench.instruments.source_restore import BASIC_RESTORE_FIELDS, SourceRestoreProfile
from wavebench.logging import CommandLogger
from wavebench.services.run_restore import restore_source_state, source_restore_coverage
from wavebench.services.source_service import SourceService
from test_run_service import make_config, write_plan
from test_source_state import make_status
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunService
from wavebench.instruments.registry import resolve_instrument_descriptor


def profile():
    return SourceRestoreProfile(True, ("source.arbitrary_upload", "source.output"),
                                tuple(sorted(BASIC_RESTORE_FIELDS)), ("arbitrary_payload",))


def test_restore_profile_rejects_false_promises():
    for kwargs in ({"supported": 1}, {"supported": True},
                   {"supported": False, "fields": ("output",)},
                   {"supported": True, "operations": ("scope.capture",), "fields": tuple(BASIC_RESTORE_FIELDS)}):
        with pytest.raises(ValueError):
            SourceRestoreProfile(**kwargs)


def test_plan_rejects_undeclared_arb_restore_before_lifecycle(tmp_path, monkeypatch):
    plan = load_run_plan(write_plan(str(tmp_path), '''[restore]
source_state = true
source_channel = 1
[[steps]]
kind = "source.arb_load"
file = "missing.npy"
frequency_hz = 1000
amplitude_vpp = 1
'''))
    for declaration in (None, SourceRestoreProfile(False)):
        descriptor = SimpleNamespace(source_restore=declaration)
        with pytest.raises(ConfigError, match="not supported"):
            source_restore_coverage(plan, descriptor, 1)
    coverage = source_restore_coverage(plan, SimpleNamespace(source_restore=profile()), 1)
    assert coverage["uncovered"] == ["arbitrary_payload"]
    assert coverage["requested"] is True
    wrong_channel = replace(plan, restore=replace(plan.restore, source_channels=(2,)))
    with pytest.raises(ConfigError, match="channel"):
        source_restore_coverage(wrong_channel, SimpleNamespace(source_restore=profile()), 1)
    no_restore = replace(plan, restore=replace(plan.restore, source_state=False))
    assert not source_restore_coverage(no_restore, SimpleNamespace(source_restore=None), 1)["requested"]
    service = RunService(make_config(str(tmp_path)), CommandLogger())
    monkeypatch.setattr(service, "_check_plan_capabilities", lambda plan: None)
    monkeypatch.setattr("wavebench.services.run_service.resolve_instrument_descriptor",
                        lambda *a, **k: SimpleNamespace(source_restore=None))
    monkeypatch.setattr(service, "_run_instrument_lifecycle", lambda plan: pytest.fail("must not open sessions"))
    with pytest.raises(ConfigError, match="not supported"):
        service.run(plan)


class NativeDriver:
    def __init__(self):
        self.status = make_status(output="ON")
        self.identity = "VENDOR,MODEL,SERIAL,1"
        self.calls = []
        self.bad_readback = False

    def idn(self):
        return self.identity

    def snapshot_basic_state(self, channel):
        return replace(self.status, channel=channel)

    def restore_basic_state(self, snapshot):
        self.calls.append(snapshot)
        self.status = replace(snapshot, function="USER") if self.bad_readback else snapshot
        return self.status


def native_service(tmp_path):
    cfg = make_config(str(tmp_path))
    base = resolve_instrument_descriptor(cfg.source.driver, expected_kind="source")
    desc = replace(base, capabilities=tuple(dict.fromkeys((*base.capabilities, "source.restore_state"))), source_restore=profile())
    driver = NativeDriver()
    return SourceService(cfg, CommandLogger(), session=driver, descriptor=desc), driver


def test_native_restore_uses_saved_target_and_safety_gate_overrides_output(tmp_path):
    service, driver = native_service(tmp_path)
    state = service.snapshot_restorable_state(1)
    driver.status = replace(driver.status, function="USER", amplitude=1.)
    assert restore_source_state([state], source_service_factory=lambda: service, force_off_channels=(1,)) is None
    assert driver.calls[0].function == "SIN"
    assert driver.calls[0].amplitude == 5.
    assert driver.calls[0].output == "OFF"
    assert state.output == "ON"
    assert state.restore_evidence["status"] == "verified"
    assert state.restore_evidence["output_override"] == "safety_gate_off"


def test_native_restore_mismatch_and_identity_change_are_not_success(tmp_path):
    service, driver = native_service(tmp_path)
    state = service.snapshot_restorable_state(1)
    driver.identity = "another instrument"
    error = restore_source_state([state], source_service_factory=lambda: service)
    assert error and not driver.calls
    assert state.restore_evidence["status"] == "failed"
    driver.identity = state.instrument_idn
    driver.bad_readback = True
    error = restore_source_state([state], source_service_factory=lambda: service)
    assert error and state.restore_evidence["status"] == "failed"


@pytest.mark.parametrize("requested,bad_readback", [(True, False), (True, True), (False, False)])
def test_run_artifact_and_report_distinguish_restore_from_uncovered_payload(tmp_path, monkeypatch, requested, bad_readback):
    from contextlib import nullcontext
    import json
    import numpy as np
    from wavebench.data.packages import load_run_package
    from wavebench.report.html import render_run_report_html
    from wavebench.services.run_service import RunInstrumentServices

    source, driver = native_service(tmp_path)
    driver.status = replace(driver.status, output="OFF")
    driver.bad_readback = bad_readback
    def upload(**kwargs):
        driver.status = replace(driver.status, function="USER", amplitude=kwargs["amplitude_vpp"])
        return driver.status
    driver.upload_dg4000_dac14_block = upload
    waveform = tmp_path / "arb.npy"
    np.save(waveform, np.array([0., 1., 0., -1.]))
    plan = load_run_plan(write_plan(str(tmp_path), f'''[restore]
source_state = {str(requested).lower()}
{'source_channel = 1' if requested else ''}
[[steps]]
kind = "source.arb_load"
file = "{waveform.as_posix()}"
channel = 1
frequency_hz = 1000
amplitude_vpp = 1
'''))
    service = RunService(source.config, CommandLogger())
    monkeypatch.setattr(service, "_run_safety_guards", lambda *a, **k: None)
    monkeypatch.setattr(service, "_run_instrument_services", lambda plan: nullcontext(RunInstrumentServices(source=source)))
    monkeypatch.setattr("wavebench.services.run_service.resolve_instrument_descriptor", lambda *a, **k: source.descriptor)
    monkeypatch.setattr("wavebench.instruments.registry.resolve_instrument_descriptor", lambda *a, **k: source.descriptor)
    if bad_readback:
        with pytest.raises(ConfigError, match="restore failed"):
            service.run(plan)
        run_path = next(tmp_path.rglob("run.json"))
    else:
        run_path = service.run(plan).run_json_path
    run = json.loads(run_path.read_text())
    assert run["steps"][0]["artifact"]["restore_coverage"]["uncovered"] == ["arbitrary_payload"]
    assert run["status"] == ("failed" if bad_readback else "ok")
    if requested:
        assert run["restore"]["results"][0]["status"] == ("failed" if bad_readback else "verified")
    else:
        assert not driver.calls and "restore" not in run
    html = render_run_report_html(load_run_package(run_path.parent))
    assert "Source restoration coverage" in html and "arbitrary_payload" in html


def test_builtin_fallback_restores_user_without_external_plugin():
    from wavebench.instruments.registry import InstrumentRegistry
    from wavebench.drivers.dg4202 import DG4202Source
    from test_dg4202 import FakeTransport

    class Transport(FakeTransport):
        def query(self, command, **kwargs):
            if command in {":SOUR2:BURS:STAT?", ":SOUR2:MOD:STAT?"}:
                return "OFF"
            return super().query(command, **kwargs)

    descriptor = InstrumentRegistry(external_entry_points=()).resolve("rigol.dg4202")
    assert descriptor.origin == "builtin" and descriptor.source_restore.supported
    transport = Transport()
    transport.state.update(out="OFF", mode="FIX", swe="OFF")
    driver = DG4202Source(transport)
    snapshot = driver.snapshot_basic_state(2)
    transport.state.update(func="USER", volt=1., offs=.2)
    restored = driver.restore_basic_state(snapshot)
    assert restored.function == "SIN" and restored.amplitude == 5. and restored.offset_v == 0.
    assert restored.output == "OFF"
    assert not transport.byte_writes


def test_native_restore_rejects_mismatched_snapshot_channel_before_write(tmp_path):
    service, driver = native_service(tmp_path)
    state = service.snapshot_restorable_state(1)
    with pytest.raises(ConfigError, match="matching driver snapshot"):
        service.restore_restorable_state(replace(state, channel=2))
    assert not driver.calls
