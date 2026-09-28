"""Opt-in, spawn-based supervision of offline analysis; never owns hardware."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import multiprocessing
from pathlib import Path
import signal
import tempfile
import time
import tomllib

from wavebench.errors import ConfigError, DataError, error_envelope


@dataclass(frozen=True)
class AnalysisExecution:
    timeout_s: float = 300.0
    grace_s: float = 2.0
    memory_bytes: int | None = None
    cgroup_root: str | None = None

    def __post_init__(self):
        for name in ('timeout_s', 'grace_s'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ConfigError(f'analysis execution {name} must be finite and positive')
            object.__setattr__(self, name, float(value))
        if self.memory_bytes is not None and (type(self.memory_bytes) is not int or not 1 <= self.memory_bytes <= 2**63 - 1):
            raise ConfigError('analysis execution memory_bytes must be an integer in [1, 2^63-1]')
        if self.cgroup_root is not None and (not isinstance(self.cgroup_root, str) or not self.cgroup_root):
            raise ConfigError('analysis execution cgroup_root must be a nonempty path')
        if self.cgroup_root is not None and self.memory_bytes is None:
            raise ConfigError('cgroup_root requires memory_bytes')

    def evidence(self):
        return {'schema': 'wavebench.analysis_execution.v1', 'start_method': 'spawn', **asdict(self)}

    def preflight(self):
        from .analysis_platform import memory_scope
        with memory_scope(self) as scope:
            if self.memory_bytes is not None:
                context = multiprocessing.get_context('spawn')
                ready = context.Event()
                probe = context.Process(target=_probe_worker, args=(ready,))
                try:
                    probe.start()
                    scope.attach(probe.pid)
                    ready.set()
                    probe.join(10)
                    if probe.is_alive() or probe.exitcode != 0:
                        raise ConfigError('analysis memory preflight worker failed')
                except OSError as exc:
                    raise ConfigError(f'cannot attach analysis memory preflight worker: {exc}') from exc
                finally:
                    if probe.pid is not None and probe.is_alive():
                        probe.kill()
                        probe.join()
                    probe.close()


def load_analysis_execution(path):
    if path is None:
        return None
    try:
        with Path(path).open('rb') as file:
            raw = file.read(65537)
        if len(raw) > 65536:
            raise ConfigError('analysis execution profile exceeds 65536 bytes')
        values = tomllib.loads(raw.decode('utf-8-sig'))
        if values.pop('schema', None) != 'wavebench.analysis_execution.v1':
            raise ConfigError('execution profile schema must be wavebench.analysis_execution.v1')
        return AnalysisExecution(**values)
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigError(f'cannot read analysis execution profile: {exc}') from exc


def _probe_worker(ready):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    ready.wait(10)


def _worker(target, kwargs, gate, cancel, result_path, limits):
    # The parent handles console interruption and records the reason.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    gate.wait()
    from wavebench.data.analysis_control import cancel_signal
    from .run_pipeline import _atomic_write_bytes
    cancel_signal.set(cancel)
    try:
        artifact = target(**kwargs)
        payload = {'artifact': artifact}
    except BaseException as exc:
        payload = {'error': error_envelope(exc, operation='analysis.worker')}
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    try:
        limits.check('max_metadata_bytes', len(raw), 'worker result')
        limits.check('max_temp_bytes', len(raw), 'worker result')
    except DataError as exc:
        # Small diagnostic envelopes use the same exhaustion exception as failure manifests.
        raw = json.dumps({'error': error_envelope(exc, operation='analysis.worker_result')}).encode('utf-8')
    _atomic_write_bytes(Path(result_path), raw)


def _read_document(path, limit):
    if not path.exists():
        return {}
    with path.open('rb') as file:
        raw = file.read(limit + 1)
    if len(raw) > limit:
        raise DataError('analysis worker document exceeds metadata budget')
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise DataError('analysis worker document must be an object')
    return payload


def supervise(target, kwargs, *, policy, run_dir, processing_dir, fields, source, limits,
              schema='wavebench.analysis_pipeline.v1', cancel_event=None):
    """Run one serializable analysis entry point, then own its terminal record."""
    from .analysis_platform import memory_scope
    from .run_pipeline import _atomic_write_json
    from .run_analysis import evaluate_expect

    context = multiprocessing.get_context('spawn')
    gate, cancel = context.Event(), context.Event()
    reason = None
    forced = False
    artifact = None
    worker_error = None
    started = time.monotonic()
    run_dir, processing_dir = Path(run_dir), Path(processing_dir)
    # The control directory is outside the worker-owned processing directory.
    if processing_dir.exists():
        raise ConfigError('supervised analysis output must be a new directory')
    control_parent = processing_dir.parent
    control_parent.mkdir(parents=True, exist_ok=True)
    exitcode = None
    memory = {'memory_backend': 'unavailable'}
    try:
        with memory_scope(policy) as scope, tempfile.TemporaryDirectory(prefix='.analysis-control-', dir=control_parent) as control:
            result_path = Path(control) / 'result.json'
            process = context.Process(target=_worker, args=(target, kwargs, gate, cancel, str(result_path), limits))
            exitcode = None
            memory = scope.evidence()
            try:
                process.start()
                scope.attach(process.pid)
                gate.set()
                deadline = started + policy.timeout_s
                while process.is_alive():
                    try:
                        if reason is None:
                            if cancel_event is not None and cancel_event.is_set():
                                reason = 'analysis_cancelled'
                            elif time.monotonic() >= deadline:
                                reason = 'analysis_timeout'
                            if reason:
                                cancel.set()
                                deadline = time.monotonic() + policy.grace_s
                        elif time.monotonic() >= deadline:
                            forced = True
                            process.terminate()
                            process.join(1)
                            if process.is_alive():
                                process.kill()
                            break
                        process.join(0.05)
                    except KeyboardInterrupt:
                        if reason is None:
                            reason = 'analysis_cancelled'
                            cancel.set()
                            deadline = time.monotonic() + policy.grace_s
                        else:
                            deadline = time.monotonic()
                process.join()
                exitcode = process.exitcode
                memory = scope.evidence()
                try:
                    payload = _read_document(result_path, max(limits.max_metadata_bytes, 65536))
                    artifact = payload.get('artifact')
                    if not isinstance(artifact, dict) or not isinstance(artifact.get('analysis_pipeline'), dict):
                        artifact = None
                    worker_error = payload.get('error')
                except (OSError, ValueError, DataError) as exc:
                    worker_error = error_envelope(exc, operation='analysis.worker_result')
                if artifact is None or exitcode != 0:
                    reason = reason or ('resource_limit_exceeded' if worker_error and worker_error.get('code') == 'resource_limit_exceeded'
                                        else 'analysis_worker_failed')
            except KeyboardInterrupt:
                reason = 'analysis_cancelled'
                forced = True
            except Exception as exc:
                reason = reason or 'analysis_worker_failed'
                worker_error = error_envelope(exc, operation='analysis.worker_start')
            finally:
                if process.pid is not None and process.is_alive():
                    process.kill()
                    process.join()
                if process.pid is not None:
                    exitcode = process.exitcode
                process.close()

    except KeyboardInterrupt:
        reason, forced = 'analysis_cancelled', True
    except Exception as exc:
        reason = reason or 'analysis_worker_failed'
        worker_error = error_envelope(exc, operation='analysis.supervisor_setup_or_cleanup')
        artifact = None

    # No worker can touch these files after join and memory-scope cleanup.
    processing_dir.mkdir(parents=True, exist_ok=True)
    for directory in (processing_dir, processing_dir / 'exports', processing_dir / 'peaks'):
        if directory.is_dir():
            for path in directory.glob('.*.tmp'):
                path.unlink(missing_ok=True)
    manifest_path = processing_dir / 'manifest.json'
    metrics_path = processing_dir / 'metrics.json'
    try:
        manifest = _read_document(manifest_path, max(limits.max_metadata_bytes, 65536))
        metrics = _read_document(metrics_path, max(limits.max_metadata_bytes, 65536)).get('metrics', {})
    except (OSError, ValueError, DataError):
        manifest, metrics = {}, {}
    source.update(manifest.get('source', {}))
    if reason == 'analysis_worker_failed' and exitcode == 0 and manifest.get('status') == 'failed' and manifest.get('error'):
        worker_error = manifest['error']
    evidence = {**policy.evidence(), **memory, 'exitcode': exitcode, 'forced': forced,
                'elapsed_s': time.monotonic() - started, 'reason': reason}
    if artifact is None:
        manifest = {'schema': schema, 'source': source, 'operations': fields['operations'],
                    'stages': [], 'warnings': [], 'exports': [],
                    'metrics': metrics_path.relative_to(run_dir).as_posix(),
                    'sampling': None, 'window': None, **manifest}
        artifact = {'analysis_pipeline': {
            'schema': schema, 'source_step': source.get('step'), 'source_status': source.get('status'),
            'manifest': manifest_path.relative_to(run_dir).as_posix(),
            'metrics': metrics_path.relative_to(run_dir).as_posix(),
            'operations': fields['operations'], 'warnings': manifest.get('warnings', []),
            'exports': manifest.get('exports', []),
        }, 'metrics': metrics}
        if manifest.get('peaks'):
            artifact['analysis_pipeline']['peaks'] = manifest['peaks']
    pipeline = artifact['analysis_pipeline']
    if reason:
        error = error_envelope(DataError(reason.replace('_', ' ')), operation='analysis.supervisor',
                               details={'exitcode': exitcode, 'forced': forced}, cause=worker_error)
        error['code'] = reason
        stage = next((f"operations[{item['index']}]" for item in manifest.get('stages', [])
                      if item.get('status') == 'running' and 'index' in item),
                     manifest.get('failed_stage') or 'supervisor')
        for item in manifest.get('stages', []):
            if item.get('status') == 'running':
                item['status'] = 'failed'
        recorded = {item.get('index') for item in manifest.get('stages', [])}
        for index, operation in enumerate(fields['operations']):
            if index not in recorded:
                manifest['stages'].append({'index': index, 'op': operation['op'], 'status': 'skipped'})
        manifest.update(status='failed', partial=bool(manifest.get('exports') or any(value is not None for value in metrics.values())),
                        failed_stage=stage, error=error)
        pipeline.update(status='failed', failed_stage=stage, error=error)
        if 'expect' in fields:
            artifact['expect'] = evaluate_expect(artifact['metrics'], fields['expect'])
    resources = manifest.setdefault('resources', {**limits.evidence(), 'work_units': 0,
                                                  'data_output_bytes': 0, 'data_output_files': 0})
    # Charge every committed data file, including one replaced just before worker death.
    data_files = [path for folder in ('exports', 'peaks')
                  for path in (processing_dir / folder).glob('*') if path.is_file() and not path.name.startswith('.')]
    resources['data_output_bytes'] = sum(path.stat().st_size for path in data_files)
    resources['data_output_files'] = len(data_files)
    if reason:
        manifest['committed_files'] = [path.relative_to(run_dir).as_posix() for path in sorted(data_files)]
    pipeline['resources'] = resources
    manifest['execution'] = pipeline['execution'] = evidence
    from wavebench.data.analysis_resources import AnalysisBudget, AnalysisResourceError
    budget = AnalysisBudget(limits)
    budget.output_bytes = resources['data_output_bytes']
    budget.output_files = resources['data_output_files']
    try:
        _atomic_write_json(metrics_path, {'schema': 'wavebench.analysis_metrics.v1', 'metrics': artifact['metrics']}, budget=budget)
        _atomic_write_json(manifest_path, manifest, budget=budget)
    except AnalysisResourceError as exc:
        error = error_envelope(exc, operation='analysis.supervisor_metadata')
        if not reason:
            manifest.update(status='failed', failed_stage='metadata', error=error)
            pipeline.update(status='failed', failed_stage='metadata', error=error)
        _atomic_write_json(metrics_path, {'schema': 'wavebench.analysis_metrics.v1', 'metrics': artifact['metrics']})
        _atomic_write_json(manifest_path, manifest)
    return artifact
