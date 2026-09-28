"""Serial explicit batches with content-verified resume and aggregate disk admission."""
from dataclasses import replace
from hashlib import sha256
from importlib.metadata import version, PackageNotFoundError
import json
from pathlib import Path
import tomllib

from wavebench import __version__
from wavebench.data.analysis_io import mapped_npy, hash_stream, read_json_bounded
from wavebench.data.analysis_resources import AnalysisLimits
from wavebench.data.packages import _capture_channels
from wavebench.data.pipeline_operations import integer, result_name
from wavebench.errors import ConfigError, DataError, error_envelope
from .analysis_execution import AnalysisExecution
from .analysis_service import load_analysis_recipe, run_analysis
from .file_lock import FileLock, FileLockError
from .run_pipeline import _atomic_write_json, _atomic_write_csv, _resolve_package_member, _sha256_file


SCHEMA = 'wavebench.analysis_batch.v1'


def source_fingerprint(capture, channel, limits):
    capture = Path(capture).resolve()
    metadata_path = _resolve_package_member(capture, 'metadata.json', label='metadata')
    metadata = read_json_bounded(metadata_path, limits)
    metadata_hash = _sha256_file(metadata_path)
    candidates = [entry for entry in _capture_channels(metadata) if entry.channel == channel]
    if len(candidates) != 1 or not candidates[0].files.get('npy'):
        raise DataError('batch source must contain exactly one requested NPY channel')
    path = _resolve_package_member(capture, candidates[0].files['npy'], label='NPY')
    with mapped_npy(path, limits, columns=2, source=True) as (_, file):
        data_hash = hash_stream(file)
    if metadata_hash != _sha256_file(metadata_path):
        raise DataError('batch source metadata changed while fingerprinting')
    return {'metadata_sha256': metadata_hash, 'npy_sha256': data_hash}


def load_batch(path, limits):
    path = Path(path).resolve()
    with path.open('rb') as file:
        raw = file.read(limits.max_metadata_bytes + 1)
    limits.check('max_metadata_bytes', len(raw), 'batch manifest')
    limits.check('max_working_bytes', len(raw) * 32 + 65536, 'batch manifest')
    try:
        data = tomllib.loads(raw.decode('utf-8-sig'))
        if set(data) != {'schema', 'recipe', 'entries', 'on_failure', 'duplicates', 'max_output_bytes'} or data['schema'] != SCHEMA:
            raise ConfigError('batch manifest requires schema, recipe, entries, on_failure, duplicates, max_output_bytes')
        if data['on_failure'] not in ('stop', 'continue') or data['duplicates'] not in ('reject', 'allow'):
            raise ConfigError('invalid batch failure or duplicate policy')
        data['max_output_bytes'] = integer(data['max_output_bytes'], 'batch max_output_bytes', 1, limits.max_output_bytes)
        entries = data['entries']
        if not isinstance(entries, list) or not 1 <= len(entries) <= min(256, limits.max_output_files):
            raise ConfigError('batch entries must contain 1..256 items within file budget')
        names, sources = set(), set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'id', 'capture', 'channel'}:
                raise ConfigError('batch entry requires id, capture and channel')
            entry['id'] = result_name(entry['id'])
            entry['channel'] = integer(entry['channel'], 'channel', 1, 65535)
            entry['capture'] = str((path.parent / entry['capture']).resolve())
            key = (entry['capture'], entry['channel'])
            if entry['id'] in names or key in sources and data['duplicates'] == 'reject':
                raise ConfigError('duplicate batch id or source')
            names.add(entry['id'])
            sources.add(key)
        data['recipe'] = str((path.parent / data['recipe']).resolve())
        return data
    except (ValueError, TypeError, DataError) as exc:
        raise ConfigError(f'invalid batch manifest: {exc}') from exc


def _inventory(root, limits):
    files = {}
    if root.exists():
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                raise ConfigError('batch results must not contain symlinks')
            if path == root / '.batch.lock':
                continue  # Windows denies reading the active lock through another handle.
            if path.is_file():
                limits.check('max_output_files', len(files) + 1, 'batch inventory')
                files[path.relative_to(root).as_posix()] = {'bytes': path.stat().st_size, 'sha256': _sha256_file(path)}
    return files


def run_batch(manifest, output, *, resource_limits=None, execution_policy=None, resume=False, cancel_event=None):
    limits = resource_limits or AnalysisLimits()
    specification = load_batch(manifest, limits)
    recipe = load_analysis_recipe(specification['recipe'], limits)
    policy = execution_policy or AnalysisExecution()  # Batch cancellation requires a supervised item.
    policy.preflight()
    output = Path(output).resolve()
    for entry in specification['entries']:
        source = Path(entry['capture'])
        if output == source or source in output.parents or output in source.parents:
            raise ConfigError('batch output must be separate from every capture package')
    if any((parent / 'metadata.json').exists() or (parent / 'run.json').exists() for parent in output.parents):
        raise ConfigError('batch output must not modify an existing capture or run')
    if output.exists() != resume:
        raise ConfigError('new batch requires a new directory; resume requires the existing batch directory')
    fingerprints, source_errors = [], []
    for entry in specification['entries']:
        try:
            fingerprints.append(source_fingerprint(entry['capture'], entry['channel'], limits))
            source_errors.append(None)
        except (OSError, ValueError, ConfigError, DataError) as exc:
            fingerprints.append(None)
            source_errors.append(error_envelope(exc, operation='analysis.batch.source'))
    libraries = {'wavebench': __version__}
    for name in ('numpy', 'scipy'):
        try:
            libraries[name] = version(name)
        except PackageNotFoundError:
            libraries[name] = None
    binding = {'specification': specification, 'recipe': recipe, 'resources': limits.evidence(),
               'execution': policy.evidence(), 'backends': libraries, 'sources': fingerprints}
    binding_hash = sha256(json.dumps(binding, sort_keys=True, allow_nan=False).encode()).hexdigest()
    output.mkdir(exist_ok=resume, parents=True)
    lock = FileLock(output / '.batch.lock')
    try:
        lock.acquire()
    except FileLockError as exc:
        raise ConfigError(f'batch is locked or file locking unavailable: {exc}') from exc
    try:
        index_path = output / 'batch.json'
        if resume:
            index = read_json_bounded(index_path, limits)
            if index.get('schema') != SCHEMA or index.get('binding_sha256') != binding_hash:
                raise ConfigError('batch inputs, recipe, resources or backend changed; use a new output directory')
            for record in index['entries']:
                for attempt in record.get('attempts', []):
                    folder = _safe_attempt(output, attempt['directory'])
                    if attempt['files'] != _inventory(folder, limits):
                        raise ConfigError('batch completed artifacts changed; use a new output directory')
        else:
            index = {'schema': SCHEMA, 'binding': binding, 'binding_sha256': binding_hash,
                     'status': 'running', 'entries': [dict(id=item['id'], status='pending', attempts=[]) for item in specification['entries']]}
        # Verify the full tree too, including attempts interrupted before index completion.
        prior_inventory = _inventory(output, limits)
        if resume and index.get('tree_files') is not None and not any(item['status'] == 'running' for item in index['entries']):
            actual_tree = {k: v for k, v in prior_inventory.items() if k not in ('batch.json', 'summary.csv', '.batch.lock')}
            if actual_tree != index['tree_files']:
                raise ConfigError('batch output tree changed; use a new output directory')
        index.pop('cancelled', None)
        index.pop('error', None)
        def save():
            index['tree_files'] = {k: v for k, v in _inventory(output, limits).items()
                                   if k not in ('batch.json', 'summary.csv', '.batch.lock')}
            _atomic_write_json(index_path, index)
            rows = [[item['id'], item['status'], json.dumps(item.get('metrics', {}), sort_keys=True),
                     item.get('directory', ''), item.get('error', {}).get('code', '')] for item in index['entries']]
            import numpy as np
            _atomic_write_csv(output / 'summary.csv', ['id', 'status', 'metrics_json', 'directory', 'error_code'], np.array(rows, dtype=object))
        save()
        # Reserve bounded room for the index and its CSV before admitting an item.
        reserve = min(limits.max_metadata_bytes, max(65536, len(specification['entries']) * 8192))
        for position, (entry, record) in enumerate(zip(specification['entries'], index['entries'])):
            if record['status'] == 'ok':
                continue
            if cancel_event is not None and cancel_event.is_set():
                index['cancelled'] = True
                break
            if source_errors[position]:
                record.update(status='failed', error=source_errors[position])
            else:
                all_files = _inventory(output, limits)
                used = sum(item['bytes'] for name, item in all_files.items() if name not in ('batch.json', 'summary.csv'))
                remaining = specification['max_output_bytes'] - used - reserve
                remaining_files = limits.max_output_files - len(all_files) - 3
                attempt_no = len(record['attempts']) + 1
                directory = f"items/{entry['id']}/attempt_{attempt_no:03d}"
                # Interrupted attempts are preserved; a new attempt never overwrites them.
                while (output / directory).exists():
                    attempt_no += 1
                    directory = f"items/{entry['id']}/attempt_{attempt_no:03d}"
                record.update(status='running', directory=directory)
                save()
                try:
                    if remaining <= 0 or remaining_files <= 0:
                        raise DataError('batch aggregate output budget exhausted')
                    item_limits = replace(limits, max_output_bytes=min(limits.max_output_bytes, remaining),
                                          max_output_files=min(limits.max_output_files, remaining_files))
                    if load_analysis_recipe(specification['recipe'], limits) != recipe:
                        raise DataError('batch recipe changed during execution; use a new output directory')
                    result = run_analysis(Path(entry['capture']), entry['channel'], Path(specification['recipe']),
                                          output / directory, resource_limits=limits, _output_limits=item_limits, execution_policy=policy,
                                          cancel_event=cancel_event)
                    if result['recipe'] != recipe:
                        raise DataError('batch recipe changed during item execution')
                    after = source_fingerprint(entry['capture'], entry['channel'], limits)
                    if after != fingerprints[position] or result['source'].get('npy_sha256', after['npy_sha256']) != after['npy_sha256']:
                        raise DataError('batch source changed during execution; result cannot be reused')
                    record.update(status=result['status'], metrics=result['artifact']['metrics'])
                    error = result.get('error') or result['artifact']['analysis_pipeline'].get('error')
                    record.pop('error', None)
                    if error:
                        record['error'] = error
                    if error and error.get('code') == 'analysis_cancelled':
                        index['cancelled'] = True
                except KeyboardInterrupt:
                    index['cancelled'] = True
                    record.update(status='failed', error={'code': 'analysis_cancelled', 'message': 'batch cancelled'})
                except (OSError, ValueError, ConfigError, DataError) as exc:
                    record.update(status='failed', error=error_envelope(exc, operation='analysis.batch.item'))
                if (output / directory).exists():
                    record['attempts'].append({'directory': directory, 'files': _inventory(output / directory, limits)})
            save()
            if index.get('cancelled') or record['status'] == 'failed' and specification['on_failure'] == 'stop':
                break
        index['status'] = 'ok' if all(item['status'] == 'ok' for item in index['entries']) else 'failed'
        save()
        actual = _inventory(output, limits)
        if sum(item['bytes'] for item in actual.values()) > specification['max_output_bytes']:
            index.update(status='failed', error={'code': 'resource_limit_exceeded', 'message': 'batch aggregate output budget exhausted'})
            save()
        return index
    finally:
        lock.release()


def _safe_attempt(output, raw):
    path = (output / raw).resolve()
    if Path(raw).is_absolute() or not path.is_relative_to(output) or path == output:
        raise ConfigError('batch attempt path escapes output')
    return path
