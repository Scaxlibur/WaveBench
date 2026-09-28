"""Independent pair entry points sharing RunPlan lifecycle and supervision artifacts."""
from pathlib import Path
import json
import tomllib
from hashlib import sha256

import numpy as np

from wavebench import __version__
from wavebench.data.analysis_control import checkpoint, cancel_signal
from wavebench.data.analysis_io import load_waveform, read_json_bounded, mapped_npy
from wavebench.data.analysis_resources import AnalysisLimits, AnalysisBudget, AnalysisResourceError
from wavebench.data.packages import _capture_channels
from wavebench.data.pipeline_operations import integer
from wavebench.data.pair_analysis import (PAIR_COLUMNS, normalize_pair_operations, validate_sync,
                                         delay_estimate, transfer_estimate, check_pair_static)
from wavebench.errors import ConfigError, DataError, error_envelope
from .run_analysis import evaluate_expect
from .run_pipeline import _atomic_write_json, _atomic_write_npy, _atomic_write_csv, _resolve_package_member, _sha256_file


PAIR_SCHEMA = 'wavebench.analysis_pair.v1'
PAIR_RECIPE_SCHEMA = 'wavebench.analysis_pair_recipe.v1'


def normalize_pair_fields(fields):
    try:
        for key in ('reference_channel', 'response_channel'):
            fields[key] = integer(fields[key], key, 1, 65535)
        if fields['reference_channel'] == fields['response_channel']:
            raise DataError('reference and response must be distinct channels')
        fields['operations'] = normalize_pair_operations(fields)
        if 'resources' in fields:
            from wavebench.data.analysis_resources import normalize_limits
            fields['resources'] = normalize_limits(fields['resources'])
        if 'expect' in fields:
            from .run_plan import _parse_expect
            fields['expect'] = _parse_expect(fields['expect'], 'pair.expect')
    except (DataError, KeyError, TypeError) as exc:
        raise ConfigError(f'invalid pair configuration: {exc}') from exc


def load_pair_recipe(path, limits):
    with Path(path).open('rb') as file:
        raw = file.read(limits.max_metadata_bytes + 1)
    limits.check('max_metadata_bytes', len(raw), 'pair recipe')
    limits.check('max_working_bytes', len(raw)*32 + 65536, 'pair recipe')
    try:
        fields = tomllib.loads(raw.decode('utf-8-sig'))
        if fields.pop('schema', None) != PAIR_RECIPE_SCHEMA or set(fields) - {'reference_channel', 'response_channel', 'operations', 'resources', 'expect'}:
            raise ConfigError('invalid pair recipe schema or fields')
        normalize_pair_fields(fields)
        check_pair_static(fields['operations'], limits.tighten(fields.get('resources')), metadata_files=3)
        return fields
    except (ValueError, TypeError) as exc:
        raise ConfigError(f'invalid pair recipe: {exc}') from exc


def ensure_pair_dependencies():
    try:
        from scipy.signal import correlate, get_window, detrend
        from scipy.fft import rfft
        if not all(callable(f) for f in (correlate, get_window, detrend, rfft)):
            raise ImportError('missing pair analysis functions')
    except ImportError as exc:
        raise ConfigError('pair analysis requires SciPy; install .[analysis]') from exc


def load_pair_source(capture, fields, limits):
    capture = Path(capture).resolve()
    meta_path = _resolve_package_member(capture, 'metadata.json', label='metadata')
    before = _sha256_file(meta_path)
    metadata = read_json_bounded(meta_path, limits)
    evidence = metadata.get('synchronization')
    if not isinstance(evidence, dict) or evidence.get('schema') != 'wavebench.capture_sync.v1' or evidence.get('kind') not in ('synthetic', 'driver_frozen_single'):
        raise DataError('pair requires explicit supported synchronization evidence')
    if evidence.get('kind') == 'driver_frozen_single':
        driver = evidence.get('driver', {})
        identity = metadata.get('instrument', {}).get('idn', '')
        parts = identity.split(',') if isinstance(identity, str) else []
        if len(parts) != 4 or parts[1].strip() != driver.get('model') or parts[3].strip() != driver.get('firmware'):
            raise DataError('driver synchronization model/firmware differs from capture identity')
    channels = (fields['reference_channel'], fields['response_channel'])
    entries = _capture_channels(metadata)
    paths, total = [], 0
    for channel in channels:
        matched = [entry for entry in entries if entry.channel == channel]
        if len(matched) != 1 or not matched[0].files.get('npy'):
            raise DataError('pair requires both distinct NPY channels in the same package')
        path = _resolve_package_member(capture, matched[0].files['npy'], label='NPY')
        with mapped_npy(path, limits, columns=2, source=True) as (mapped, _):
            total += len(mapped)
            limits.check('max_working_bytes', total*64+65536, 'pair sources')
        paths.append(path)
    if paths[0] == paths[1]:
        raise DataError('pair channels cannot refer to the same NPY file')
    x, hx = load_waveform(paths[0], limits)
    y, hy = load_waveform(paths[1], limits)
    axes = validate_sync(x, y, evidence, channels)
    if before != _sha256_file(meta_path) or hx != _sha256_file(paths[0]) or hy != _sha256_file(paths[1]):
        raise DataError('pair synchronization metadata changed during loading')
    source = {'kind': 'capture_package_pair', 'package': str(capture), 'status': metadata.get('status'),
              'metadata_sha256': before, 'reference_channel': channels[0], 'response_channel': channels[1],
              'reference_npy': paths[0].relative_to(capture).as_posix(), 'reference_sha256': hx,
              'response_npy': paths[1].relative_to(capture).as_posix(), 'response_sha256': hy,
              'synchronization': evidence, 'axes': axes}
    return source, x, y


def pair_check(capture, recipe, *, resource_limits=None, execution_policy=None):
    limits = resource_limits or AnalysisLimits()
    fields = load_pair_recipe(recipe, limits)
    limits = limits.tighten(fields.get('resources'))
    ensure_pair_dependencies()
    if execution_policy:
        execution_policy.preflight()
    source, x, y = load_pair_source(capture, fields, limits)
    # Source evidence is validated offline; estimator admission uses actual sample counts.
    budget = AnalysisBudget(limits)
    output_bins = None
    for operation in fields['operations']:
        if operation['op'] == 'transfer':
            budget.stage(dict(op='psd', average='mean', **{k: operation[k] for k in ('nperseg','noverlap','nfft')}), len(x.time_s)*2)
            if len(x.time_s) < 2*operation['nperseg']-operation['noverlap']:
                raise DataError('pair transfer requires at least two complete Welch segments')
            output_bins = operation['nfft']//2+1
            limits.check('max_working_bytes', 128*len(x.time_s)+256*operation['nfft']+128*operation['nperseg'], 'pair transfer')
        elif operation['op'] == 'delay':
            length = 1 << (2*len(x.time_s)-2).bit_length()
            limits.check('max_fft_length', length, 'pair correlation')
            limits.check('max_working_bytes', 192*len(x.time_s)+64*length, 'pair correlation')
            lag = int(min(len(x.time_s)-1, operation['max_lag_s']/source['axes']['sample_interval_s'],
                          np.floor(len(x.time_s)*(1-operation['min_overlap_ratio']))))
            limits.check('max_peak_candidates', 2*lag+1, 'pair correlation')
            limits.check('max_working_bytes', 192*len(x.time_s)+64*length+256*(2*lag+1), 'pair candidates')
            work = 24*length*length.bit_length()
            limits.check('max_work_units', budget.work_units+work, 'pair correlation')
            budget.work_units += work
        else:
            for format in operation['formats']:
                estimate = output_bins*8*(8 if format == 'npy' else 32)+1024
                budget.pending_file(estimate)
                budget.committed_file(estimate)
    return {'schema': 'wavebench.analysis_pair_check.v1', 'status': 'ok', 'source': source, 'recipe': fields,
            'resources': limits.evidence()}


def pair_run(capture, recipe, output, *, resource_limits=None, execution_policy=None, cancel_event=None):
    limits = resource_limits or AnalysisLimits()
    fields = load_pair_recipe(recipe, limits)
    limits = limits.tighten(fields.get('resources'))
    ensure_pair_dependencies()
    if execution_policy:
        execution_policy.preflight()
    capture, output = Path(capture).resolve(), Path(output).resolve()
    if output.exists() or output == capture or capture in output.parents or output in capture.parents:
        raise ConfigError('pair output must be new and separate from source capture')
    if any((parent/'run.json').exists() or (parent/'metadata.json').exists() for parent in output.parents):
        raise ConfigError('pair output must not modify an existing capture or run')
    source = {'kind': 'capture_package_pair', 'package': str(capture), 'status': None}
    artifact = execute_pair(run_dir=output, processing_dir=output, capture=capture, fields=fields,
                            limits=limits, source=source, execution_policy=execution_policy, cancel_event=cancel_event)
    result = {'schema': 'wavebench.analysis.v1', 'analysis_kind': 'pair', 'wavebench_version': __version__,
              'status': 'ok' if artifact['analysis_pipeline']['status'] == 'ok' and artifact.get('expect', {}).get('status', 'ok') == 'ok' else 'failed',
              'source': source, 'recipe': fields, 'recipe_sha256': sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest(), 'artifact': artifact}
    budget = AnalysisBudget(limits)
    resources = artifact['analysis_pipeline']['resources']
    budget.output_bytes = resources['data_output_bytes'] + sum((output/name).stat().st_size for name in ('manifest.json', 'metrics.json'))
    budget.output_files = resources['data_output_files']+2
    try:
        _atomic_write_json(output/'analysis.json', result, budget=budget)
    except AnalysisResourceError as exc:
        result.update(status='failed', error=error_envelope(exc, operation='pair.result'))
        _atomic_write_json(output/'analysis.json', result)
    return result


def execute_pair(*, run_dir, processing_dir, capture, fields, limits, source, execution_policy=None, cancel_event=None):
    check_pair_static(fields['operations'], limits)
    if execution_policy:
        from .analysis_execution import supervise
        return supervise(execute_pair, dict(run_dir=run_dir, processing_dir=processing_dir, capture=capture,
            fields=fields, limits=limits, source=source), policy=execution_policy, run_dir=run_dir,
            processing_dir=processing_dir, fields=fields, source=source, limits=limits,
            schema=PAIR_SCHEMA, cancel_event=cancel_event)
    processing_dir.mkdir(parents=True, exist_ok=False)
    budget = AnalysisBudget(limits)
    metrics = {f"{op['name']}_{key}": None for op in fields['operations'] if op['op'] != 'export' for key in op['metrics']}
    manifest = {'schema': PAIR_SCHEMA, 'status': 'running', 'source': source, 'operations': fields['operations'],
                'stages': [], 'warnings': [], 'exports': [], 'metrics': (processing_dir/'metrics.json').relative_to(run_dir).as_posix()}
    def save(budgeted=False):
        manifest['resources'] = {**limits.evidence(), 'work_units': budget.work_units,
                                 'data_output_bytes': budget.output_bytes, 'data_output_files': budget.output_files}
        _atomic_write_json(processing_dir/'metrics.json', {'schema': 'wavebench.analysis_metrics.v1', 'metrics': metrics}, budget=budget if budgeted else None)
        _atomic_write_json(processing_dir/'manifest.json', manifest, budget=budget if budgeted else None)
    def progress():
        if cancel_signal.get() is not None:
            save()
    stage, spectrum = None, None
    try:
        progress()
        checkpoint()
        details, x, y = load_pair_source(capture, fields, limits)
        source_status = source.get('status')
        source.update(details)
        if 'step' in source:
            source['status'] = source_status
        manifest['sampling'] = details['axes']
        for index, operation in enumerate(fields['operations']):
            stage = {'index': index, 'op': operation['op'], 'status': 'running'}
            manifest['stages'].append(stage)
            progress()
            checkpoint()
            if operation['op'] == 'delay':
                measured, evidence = delay_estimate(x, y, operation, budget)
                metrics.update(measured)
                stage['measurement'] = evidence
            elif operation['op'] == 'transfer':
                spectrum = transfer_estimate(x, y, operation, budget)
                metrics.update(spectrum.metrics)
                stage['measurement'] = spectrum.evidence
            else:
                directory = processing_dir/'exports'
                directory.mkdir(exist_ok=True)
                for format in operation['formats']:
                    checkpoint()
                    path = directory/f"{operation['name']}.{format}"
                    if format == 'npy':
                        _atomic_write_npy(path, spectrum.data, budget=budget)
                    else:
                        def blocks():
                            for start in range(0, len(spectrum.data), 4096):
                                block = spectrum.data[start:start+4096]
                                cells = block.astype(object)
                                cells[~np.isfinite(block)] = ''
                                yield cells
                        _atomic_write_csv(path, PAIR_COLUMNS, None, blocks=blocks, budget=budget)
                    manifest['exports'].append({'name': operation['name'], 'format': format,
                        'path': path.relative_to(run_dir).as_posix(), 'sha256': _sha256_file(path),
                        'columns': PAIR_COLUMNS, 'domain': 'pair', 'invalid_values': 'nan_with_valid_mask'})
                    progress()
            if 'measurement' in stage:
                manifest['warnings'].extend(stage['measurement'].get('warnings', []))
            stage['status'] = 'ok'
            progress()
        manifest['status'] = 'ok'
    except Exception as exc:
        if stage:
            stage['status'] = 'failed'
        manifest.update(status='failed', failed_stage=f"operations[{stage['index']}]" if stage else 'source',
                        error=error_envelope(exc, operation='analysis.pair'))
    manifest['partial'] = manifest['status'] == 'failed' and bool(manifest['exports'])
    try:
        save(budgeted=True)
    except AnalysisResourceError as exc:
        manifest.update(status='failed', failed_stage='metadata', error=error_envelope(exc, operation='pair.metadata'))
        # Preserve data-only counters after an unsuccessful metadata write.
        budget.output_bytes = manifest['resources']['data_output_bytes']
        budget.output_files = manifest['resources']['data_output_files']
        save()
    pipeline = {key: manifest[key] for key in ('schema', 'status', 'operations', 'warnings', 'exports', 'resources')}
    pipeline.update(manifest=(processing_dir/'manifest.json').relative_to(run_dir).as_posix(),
                    metrics=(processing_dir/'metrics.json').relative_to(run_dir).as_posix(),
                    source_step=source.get('step'), source_status=source.get('status'))
    for key in ('error', 'failed_stage'):
        if key in manifest:
            pipeline[key] = manifest[key]
    artifact = {'analysis_pipeline': pipeline, 'metrics': metrics}
    if 'expect' in fields:
        artifact['expect'] = evaluate_expect(metrics, fields['expect'])
    return artifact


def execute_pair_step(*, run_dir, step, source_step, source_record, resource_limits=None, execution_policy=None, cancel_event=None):
    if source_record is None or not source_record.artifact.get('package'):
        raise DataError('pair source capture was not executed or has no package')
    # Capture records historically store paths relative to the process working directory.
    package = Path(source_record.artifact['package']).resolve()
    limits = (resource_limits or AnalysisLimits()).tighten(step.fields.get('resources'))
    return execute_pair(run_dir=run_dir, processing_dir=run_dir/'processing'/f"{step.index:02d}_{step.id or 'analysis_pair'}",
        capture=package, fields=step.fields, limits=limits,
        source={'step': source_step.id, 'step_index': source_step.index, 'status': source_record.status},
        execution_policy=execution_policy, cancel_event=cancel_event)
