from dataclasses import replace
import json
import multiprocessing
import os
import sys
import threading
import time

import pytest

from wavebench.data.analysis_resources import AnalysisLimits
from wavebench.errors import ConfigError
from wavebench.services.analysis_execution import AnalysisExecution, load_analysis_execution, supervise
from wavebench.services.analysis_service import run_analysis
from test_analysis_service import analysis_input as analysis_input


def _slow_pipeline(*, output, cooperative=False):
    import numpy as np
    import wavebench.services.run_pipeline as pipeline
    from wavebench.data.analysis_control import checkpoint

    def slow(signal):
        (output / 'ready').write_text('ready')
        while True:
            if cooperative:
                checkpoint()
            time.sleep(0.01)
    pipeline.remove_dc = slow
    return pipeline.execute_pipeline(run_dir=output, processing_dir=output, fields=FIELDS,
        source={'status': 'ok'}, load_source=lambda: ({}, np.column_stack((np.arange(16.), np.arange(16.)))))


FIELDS = {'operations': [{'op': 'export', 'name': 'before', 'formats': ['npy']}, {'op': 'remove_dc'}]}


def _crash():
    os._exit(7)


def test_execution_profile_strict(tmp_path):
    for kwargs in ({'timeout_s': 0}, {'grace_s': True}, {'timeout_s': float('inf')},
                   {'memory_bytes': False}, {'memory_bytes': -1}, {'cgroup_root': '/tmp'}):
        with pytest.raises(ConfigError):
            AnalysisExecution(**kwargs)
    path = tmp_path / 'execution.toml'
    path.write_text('schema="wavebench.analysis_execution.v1"\ntimeout_s=1.5\n')
    assert load_analysis_execution(path).timeout_s == 1.5
    path.write_text('schema="wavebench.analysis_execution.v1"\nunknown=2\n')
    with pytest.raises(ConfigError):
        load_analysis_execution(path)


def test_spawn_offline_matches_inline(tmp_path, analysis_input):
    capture, recipe = analysis_input
    inline = run_analysis(capture, 1, recipe, tmp_path / 'inline')
    spawned = run_analysis(capture, 1, recipe, tmp_path / 'spawn', execution_policy=AnalysisExecution())
    assert spawned['status'] == inline['status'] == 'ok'
    assert spawned['artifact']['metrics'] == inline['artifact']['metrics']
    assert spawned['source']['npy_sha256'] == inline['source']['npy_sha256']
    info = spawned['artifact']['analysis_pipeline']['execution']
    assert info['start_method'] == 'spawn' and info['exitcode'] == 0 and not info['forced']
    assert not list(tmp_path.glob('.analysis-control-*'))


@pytest.mark.parametrize('cooperative', [False, True])
def test_cancel_retains_completed_export_and_finalizes(tmp_path, cooperative):
    output = tmp_path / 'output'
    cancelled = threading.Event()
    def request_cancel():
        for _ in range(2000):
            if (output / 'ready').exists():
                cancelled.set()
                return
            time.sleep(0.01)
    thread = threading.Thread(target=request_cancel, daemon=True)
    thread.start()
    # Cooperative shutdown includes checkpoint writes and process teardown on Windows.
    grace_s = 10 if cooperative else 0.5
    artifact = supervise(_slow_pipeline, dict(output=output, cooperative=cooperative),
        policy=AnalysisExecution(timeout_s=30, grace_s=grace_s), run_dir=output, processing_dir=output,
        fields=FIELDS, source={'status': 'ok'}, limits=AnalysisLimits(), cancel_event=cancelled)
    thread.join(1)
    info = artifact['analysis_pipeline']
    assert info['error']['code'] == 'analysis_cancelled'
    assert info['execution']['forced'] is not cooperative
    assert info['exports'][0]['path'] == 'exports/before.npy'
    assert (output / 'exports/before.npy').exists()
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['status'] == 'failed' and manifest['partial']
    assert not list(output.rglob('.*.tmp'))
    assert not multiprocessing.active_children()


def test_timeout_and_abnormal_exit(tmp_path):
    for target, kwargs, code, policy in (
        (_slow_pipeline, {'output': tmp_path / 'timeout'}, 'analysis_timeout', AnalysisExecution(timeout_s=3, grace_s=0.1)),
        (_crash, {}, 'analysis_worker_failed', AnalysisExecution()),
    ):
        output = tmp_path / ('timeout' if target is _slow_pipeline else 'crash')
        artifact = supervise(target, kwargs, policy=policy, run_dir=output, processing_dir=output,
                             fields=FIELDS, source={'status': 'ok'}, limits=AnalysisLimits())
        assert artifact['analysis_pipeline']['error']['code'] == code
        assert json.loads((output / 'manifest.json').read_text())['status'] == 'failed'


def test_execution_intent_v3_binds_policy(tmp_path):
    from wavebench.services.execution_intent import build_execution_intent, verify_execution_intent
    from wavebench.services.run_plan import load_run_plan
    from test_run_service import make_config
    path = tmp_path / 'plan.toml'
    path.write_text('[[steps]]\nkind="sleep"\nduration_s=0.01\n')
    plan, config = load_run_plan(path), make_config(tmp_path)
    policy = AnalysisExecution()
    legacy = build_execution_intent(plan, config)
    intent = build_execution_intent(plan, config, execution_policy=policy)
    assert legacy.schema == 'wavebench.execution_intent.v1'
    assert intent.schema == 'wavebench.execution_intent.v3'
    verify_execution_intent(intent.as_dict(), plan, config, execution_policy=policy)
    with pytest.raises(Exception, match='does not match'):
        verify_execution_intent(intent.as_dict(), plan, config, execution_policy=replace(policy, timeout_s=42))


def test_hard_limit_unsupported_before_output(tmp_path, analysis_input):
    if sys.platform != 'linux':
        pytest.skip('Linux preflight contract')
    capture, recipe = analysis_input
    with pytest.raises(ConfigError, match='delegated cgroup_root'):
        run_analysis(capture, 1, recipe, tmp_path / 'output', execution_policy=AnalysisExecution(memory_bytes=1024**3))
    assert not (tmp_path / 'output').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native Job Object; PR workflow')
def test_windows_native_job_limit(tmp_path, analysis_input):
    capture, recipe = analysis_input
    result = run_analysis(capture, 1, recipe, tmp_path / 'job',
                          execution_policy=AnalysisExecution(memory_bytes=2 * 1024**3))
    assert result['status'] == 'ok'
    assert result['artifact']['analysis_pipeline']['execution']['memory_backend'] == 'windows_job_object'


@pytest.mark.skipif(sys.platform != 'linux' or not os.getenv('WAVEBENCH_TEST_CGROUP_ROOT'),
                    reason='requires explicitly delegated test cgroup')
def test_linux_native_cgroup(tmp_path, analysis_input):
    capture, recipe = analysis_input
    result = run_analysis(capture, 1, recipe, tmp_path / 'cgroup', execution_policy=AnalysisExecution(
        memory_bytes=1024**3, cgroup_root=os.environ['WAVEBENCH_TEST_CGROUP_ROOT']))
    assert result['status'] == 'ok'
    assert result['artifact']['analysis_pipeline']['execution']['memory_backend'] == 'linux_cgroup_v2'


def test_spawn_starts_after_hardware_cleanup(tmp_path):
    from unittest.mock import patch
    from test_run_service_analysis import PhaseRunService, _capture_record, _plan
    from test_run_service import make_config
    from wavebench.logging import CommandLogger
    import wavebench.services.analysis_execution as execution
    events = []
    service = PhaseRunService(config=make_config(str(tmp_path)), logger=CommandLogger(),
        capture_record=_capture_record(str(tmp_path)), events=events, analysis_execution=AnalysisExecution())
    original = execution.supervise
    def tracked(*args, **kwargs):
        assert events[-1] == 'session_and_lease_closed'
        events.append('spawn')
        return original(*args, **kwargs)
    with patch.object(execution, 'supervise', side_effect=tracked):
        result = service.run(_plan(str(tmp_path)))
    assert result.steps[-1].status == 'ok'
    assert result.steps[-1].artifact['analysis_pipeline']['execution']['exitcode'] == 0
    assert events.index('session_and_lease_closed') < events.index('spawn')


@pytest.mark.parametrize('cancelled', [False, True])
def test_supervised_failure_continue_and_cancel_stop_suffix(tmp_path, cancelled):
    from test_run_service_analysis import PhaseRunService, _capture_record, _plan
    from test_run_service import make_config
    from wavebench.logging import CommandLogger
    plan = _plan(str(tmp_path), two_analyses=True)
    plan.steps[1].fields['on_failure'] = 'continue'
    cancel = threading.Event()
    if cancelled:
        cancel.set()
    events = []
    service = PhaseRunService(config=make_config(str(tmp_path)), logger=CommandLogger(),
        capture_record=_capture_record(str(tmp_path)), events=events,
        analysis_execution=AnalysisExecution(timeout_s=0.001, grace_s=0.01), analysis_cancel_event=cancel)
    result = service.run(plan)
    assert len(result.steps) == (2 if cancelled else 3)
    assert result.steps[1].status == 'failed'
    assert events.count('hardware:capture_main') == 1
    expected = 'analysis_cancelled' if cancelled else 'analysis_timeout'
    assert result.steps[1].artifact['analysis_pipeline']['error']['code'] == expected


def test_cgroup_contract_with_controlled_files(tmp_path, monkeypatch):
    from pathlib import Path
    from wavebench.services.analysis_platform import _LinuxMemoryScope
    root = tmp_path / 'delegated'
    root.mkdir()
    (root / 'cgroup.controllers').write_text('memory')
    original_mkdir, original_rmdir = Path.mkdir, Path.rmdir
    def mkdir(path, *args, **kwargs):
        original_mkdir(path, *args, **kwargs)
        if path.parent == root:
            for name in ('memory.max', 'memory.swap.max', 'cgroup.procs', 'cgroup.kill'):
                (path / name).write_text('')
            (path / 'memory.events').write_text('oom 1\noom_kill 1\n')
    def rmdir(path):
        if path.parent == root:
            assert (path / 'cgroup.kill').read_text() == '1'
            for file in path.iterdir():
                file.unlink()
        original_rmdir(path)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    monkeypatch.setattr(Path, 'rmdir', rmdir)
    scope = _LinuxMemoryScope(AnalysisExecution(memory_bytes=123456, cgroup_root=str(root)))
    assert (scope.path / 'memory.max').read_text() == '123456'
    assert (scope.path / 'memory.swap.max').read_text() == '0'
    scope.attach(42)
    assert (scope.path / 'cgroup.procs').read_text() == '42'
    assert scope.evidence()['oom_kill_count'] == 1
    scope.close()
    assert list(root.iterdir()) == [root / 'cgroup.controllers']


def test_supervised_metadata_budget_is_not_bypassed(tmp_path, analysis_input):
    capture, recipe = analysis_input
    result = run_analysis(capture, 1, recipe, tmp_path / 'small',
        resource_limits=AnalysisLimits(max_metadata_bytes=1024), execution_policy=AnalysisExecution())
    assert result['status'] == 'failed'
    assert result['artifact']['analysis_pipeline']['error']['code'] == 'resource_limit_exceeded'


def test_cli_execution_profile(tmp_path, analysis_input):
    from wavebench.cli import main
    capture, recipe = analysis_input
    profile = tmp_path / 'execution.toml'
    profile.write_text('schema="wavebench.analysis_execution.v1"\ntimeout_s=10\n')
    assert main(['analysis', 'run', '--capture', str(capture), '--channel', '1',
                 '--recipe', str(recipe), '--output', str(tmp_path / 'cli'),
                 '--analysis-execution', str(profile)]) == 0


def _allocation_probe():
    bytearray(512 * 1024**2)
    raise RuntimeError('allocation unexpectedly exceeded the hard limit')


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native memory enforcement; PR workflow')
def test_windows_job_rejects_allocation(tmp_path):
    output = tmp_path / 'limit'
    policy = AnalysisExecution(memory_bytes=384 * 1024**2)
    policy.preflight()
    artifact = supervise(_allocation_probe, {}, policy=policy, run_dir=output,
        processing_dir=output, fields=FIELDS, source={'status': 'ok'}, limits=AnalysisLimits())
    error = artifact['analysis_pipeline']['error']
    assert error['code'] == 'analysis_worker_failed'
    assert error['cause']['type'] == 'MemoryError'


def test_r2_segment_cancellation_before_next_backend_call():
    import numpy as np
    from unittest.mock import patch
    from wavebench.data.analysis_control import cancel_signal, AnalysisCancelled
    from wavebench.data.signal_pipeline import validate_waveform, welch_psd
    from scipy.signal import welch
    signal = validate_waveform(np.column_stack((np.arange(1000.), np.ones(1000))))
    class CancelAfterTwoSegments:
        calls = 0
        def is_set(self):
            self.calls += 1
            return self.calls > 2
    token = cancel_signal.set(CancelAfterTwoSegments())
    try:
        with patch('scipy.signal.welch', wraps=welch) as backend:
            with pytest.raises(AnalysisCancelled):
                welch_psd(signal, method='welch', window='hann', nperseg=32,
                          noverlap=16, nfft=32, detrend='none', average='mean')
            assert backend.call_count == 2
    finally:
        cancel_signal.reset(token)


def test_hard_limit_check_precedes_hardware(tmp_path):
    if sys.platform != 'linux':
        pytest.skip('Linux delegation preflight')
    from unittest.mock import patch
    from wavebench.services.run_service import RunService
    from wavebench.logging import CommandLogger
    from test_run_service_analysis import _plan
    from test_run_service import make_config
    service = RunService(make_config(str(tmp_path)), CommandLogger(),
                         analysis_execution=AnalysisExecution(memory_bytes=1024**3))
    with patch.object(service, '_run_instrument_services', side_effect=AssertionError('hardware reached')):
        with pytest.raises(ConfigError, match='delegated cgroup_root'):
            service.run(_plan(str(tmp_path)))


def test_worker_start_failure_has_terminal_artifact(tmp_path):
    output = tmp_path / 'unpicklable'
    artifact = supervise(lambda: None, {}, policy=AnalysisExecution(), run_dir=output,
        processing_dir=output, fields=FIELDS, source={'status': 'ok'}, limits=AnalysisLimits())
    assert artifact['analysis_pipeline']['error']['code'] == 'analysis_worker_failed'
    assert json.loads((output / 'manifest.json').read_text())['status'] == 'failed'


def test_existing_output_is_not_cleaned(tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    marker = output / '.do-not-touch.tmp'
    marker.write_text('existing')
    with pytest.raises(ConfigError, match='new directory'):
        supervise(_crash, {}, policy=AnalysisExecution(), run_dir=output,
            processing_dir=output, fields=FIELDS, source={'status': 'ok'}, limits=AnalysisLimits())
    assert marker.read_text() == 'existing'
