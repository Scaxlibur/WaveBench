"""Evidence-gated pair estimators; positive delay means response arrives later."""
from dataclasses import dataclass
import numpy as np

from wavebench.data.analysis_control import checkpoint
from wavebench.data.pipeline_operations import integer, number, result_name
from wavebench.data.signal_pipeline import _uniform_sample_interval, normalize_psd_parameters
from wavebench.errors import DataError


DELAY_METRICS = {'response_delay_samples', 'response_delay_s', 'correlation', 'polarity', 'overlap_samples'}
TRANSFER_METRICS = {'mean_coherence', 'min_coherence', 'valid_bin_count', 'coherent_bin_count'}
PAIR_COLUMNS = ['frequency_hz', 'real_v', 'imaginary_v', 'gain_db', 'phase_rad', 'coherence', 'valid', 'coherent']


def normalize_pair_operations(fields):
    operations = fields.get('operations')
    if not isinstance(operations, list) or not operations:
        raise DataError('pair operations must be a nonempty array')
    normalized, measured, names, exports = [], set(), set(), set()
    seen_transfer = seen_delay = False
    for raw in operations:
        if not isinstance(raw, dict):
            raise DataError('pair operation must be a table')
        op = raw.get('op')
        if op == 'delay':
            required = {'op', 'name', 'max_lag_s', 'remove_mean', 'polarity', 'min_overlap_ratio', 'min_correlation', 'ambiguity_delta', 'metrics'}
            if set(raw) != required or seen_delay or seen_transfer:
                raise DataError('delay requires documented fields, appears once before transfer')
            seen_delay = True
            out = dict(raw, name=result_name(raw['name']))
            if type(raw['remove_mean']) is not bool or raw['polarity'] not in ('same', 'either'):
                raise DataError('delay requires explicit boolean remove_mean and same/either polarity')
            for key in ('max_lag_s', 'min_overlap_ratio', 'min_correlation', 'ambiguity_delta'):
                out[key] = number(raw[key], key)
            if not 0 < out['min_overlap_ratio'] <= 1 or not 0 < out['min_correlation'] <= 1 or not 0 <= out['ambiguity_delta'] < 1:
                raise DataError('delay overlap/correlation/ambiguity thresholds outside [0,1]')
            metrics = DELAY_METRICS
        elif op == 'transfer':
            required = {'op', 'name', 'window', 'nperseg', 'noverlap', 'nfft', 'detrend', 'min_reference_density',
                        'min_response_density', 'min_coherence', 'unwrap_phase', 'metrics'}
            if set(raw) != required or seen_transfer:
                raise DataError('transfer requires documented fields and appears once')
            seen_transfer = True
            out = dict(raw, name=result_name(raw['name']))
            params = normalize_psd_parameters(method='welch', average='mean', **{key: raw[key] for key in ('window', 'nperseg', 'noverlap', 'nfft', 'detrend')})
            out.update({key: value for key, value in params.items() if key not in ('method', 'average')})
            for key in ('min_reference_density', 'min_response_density', 'min_coherence'):
                out[key] = number(raw[key], key)
            if out['min_reference_density'] <= 0 or out['min_response_density'] <= 0 or not 0 <= out['min_coherence'] <= 1 or type(raw['unwrap_phase']) is not bool:
                raise DataError('transfer requires positive density gates, coherence in [0,1], and boolean unwrap_phase')
            metrics = TRANSFER_METRICS
        elif op == 'export':
            if set(raw) != {'op', 'name', 'formats'} or not seen_transfer:
                raise DataError('pair export must follow transfer')
            name = result_name(raw['name'])
            if name in exports or not isinstance(raw['formats'], list) or not raw['formats'] or any(fmt not in ('npy', 'csv') for fmt in raw['formats']) or len(set(raw['formats'])) != len(raw['formats']):
                raise DataError('pair export names and formats must be unique npy/csv')
            exports.add(name)
            normalized.append(dict(raw))
            continue
        else:
            raise DataError('pair supports delay, transfer and export')
        chosen = raw['metrics']
        if not isinstance(chosen, list) or not chosen or any(not isinstance(m, str) or m not in metrics for m in chosen) or len(set(chosen)) != len(chosen):
            raise DataError('pair metrics must select distinct documented names')
        if out['name'] in names:
            raise DataError('pair measurement names must be unique')
        names.add(out['name'])
        measured.update(f"{out['name']}_{metric}" for metric in chosen)
        normalized.append(out)
    if not (seen_delay or seen_transfer):
        raise DataError('pair requires delay or transfer')
    if set(fields.get('expect', {})) - measured:
        raise DataError('pair expect must select measured metrics')
    return normalized


def check_pair_static(operations, limits, metadata_files=2):
    limits.check('max_operations', len(operations), 'pair plan')
    limits.check('max_output_files', metadata_files + sum(len(op['formats']) for op in operations if op['op'] == 'export'), 'pair plan')
    for op in operations:
        if op['op'] == 'transfer':
            limits.check('max_fft_length', op['nfft'], 'pair plan')


def validate_sync(reference, response, evidence, channels):
    if not isinstance(evidence, dict) or evidence.get('schema') != 'wavebench.capture_sync.v1':
        raise DataError('pair source requires wavebench.capture_sync.v1 synchronization evidence')
    kind = evidence.get('kind')
    if kind not in ('synthetic', 'driver_frozen_single'):
        raise DataError('unsupported synchronization kind; synthetic or driver_frozen_single required')
    if kind == 'driver_frozen_single':
        driver, procedure = evidence.get('driver'), evidence.get('procedure')
        if (not isinstance(driver, dict) or not all(isinstance(driver.get(k), str) and driver[k] for k in ('id', 'model', 'firmware'))
                or not isinstance(procedure, dict) or not isinstance(procedure.get('contract'), str) or not procedure['contract']
                or type(procedure.get('single_count')) is not int or procedure['single_count'] != 1
                or any(procedure.get(k) is not True for k in ('single_opc', 'stop_opc_before', 'stop_opc_after'))
                or procedure.get('reads') != [{'channel': ch, 'configuration_unchanged': True} for ch in sorted(channels)]
                or not isinstance(procedure.get('configuration'), dict) or not procedure['configuration']):
            raise DataError('driver frozen-single proof is incomplete')
        producer = evidence.get('producer', {})
        if not isinstance(producer, dict) or producer.get('name') != driver['id'] or producer.get('version') != procedure['contract']:
            raise DataError('driver proof producer and procedure do not match')
    if evidence.get('status') != 'verified':
        raise DataError('synchronization evidence must be verified')
    producer, group, guarantees = evidence.get('producer', {}), evidence.get('acquisition_group', {}), evidence.get('guarantees', {})
    if (not isinstance(producer, dict) or not all(isinstance(producer.get(k), str) and producer[k] for k in ('name', 'version'))
            or not isinstance(group, dict) or group.get('source') != kind or not isinstance(group.get('id'), str) or not group['id']
            or not isinstance(guarantees, dict) or guarantees.get('single_record') is not True or guarantees.get('frozen_read') is not True
            or any(not isinstance(evidence.get(k), str) or not evidence[k] for k in ('timebase_id', 'record_id'))):
        raise DataError('incomplete synchronization provenance, timebase or acquisition guarantee')
    if len(reference.time_s) != len(response.time_s):
        raise DataError('pair sample counts differ; automatic alignment is not supported')
    dt = _uniform_sample_interval(reference, 'pair reference')
    other_dt = _uniform_sample_interval(response, 'pair response')
    tolerance = dt * 1e-6
    if abs(dt - other_dt) > tolerance:
        raise DataError('pair sample intervals differ')
    channel_evidence = evidence.get('channels', {})
    if not isinstance(channel_evidence, dict):
        raise DataError('synchronization channel evidence must be a table')
    for channel, signal in zip(channels, (reference, response)):
        item = channel_evidence.get(str(channel), {})
        if not isinstance(item, dict) or set(item) != {'time_start_s', 'sample_interval_s', 'samples', 'skew_s', 'uncertainty_s'}:
            raise DataError('incomplete channel synchronization evidence')
        if integer(item['samples'], 'sync samples', 1, 2**63-1) != len(signal.time_s):
            raise DataError('synchronization sample count disagrees with NPY')
        for key in ('time_start_s', 'sample_interval_s', 'skew_s', 'uncertainty_s'):
            if kind == 'driver_frozen_single' and key in ('skew_s', 'uncertainty_s') and item[key] is None:
                continue
            if type(item[key]) not in (int, float) or not np.isfinite(item[key]):
                raise DataError('synchronization values must be finite')
        if (item['uncertainty_s'] is not None and item['uncertainty_s'] < 0) or abs(item['sample_interval_s'] - dt) > tolerance or abs(item['time_start_s'] - signal.time_s[0]) > tolerance:
            raise DataError('synchronization time origin, interval or uncertainty disagrees with NPY')
    for start in range(0, len(reference.time_s), 4096):
        checkpoint()
        if np.any(np.abs(reference.time_s[start:start+4096] - response.time_s[start:start+4096]) > tolerance):
            raise DataError('pair time axes differ beyond dt * 1e-6')
    return {'samples': len(reference.time_s), 'sample_interval_s': dt, 'axis_atol_s': tolerance,
            'evidence_kind': kind, 'delay_scope': 'measurement_chain_uncalibrated'}


def delay_estimate(x, y, operation, budget):
    from scipy.signal import correlate
    n = len(x.time_s)
    dt = _uniform_sample_interval(x, 'pair delay')
    max_lag = int(min(n - 1, np.floor(operation['max_lag_s'] / dt)))
    effective_lag = min(max_lag, int(np.floor(n*(1-operation['min_overlap_ratio']))))
    budget.limits.check('max_peak_candidates', 2*effective_lag+1, 'pair delay candidates')
    fft_length = 1 << (2*n - 2).bit_length()
    budget.limits.check('max_fft_length', fft_length, 'pair correlation')
    budget.limits.check('max_working_bytes', 192*n + 64*fft_length, 'pair correlation')
    budget.limits.check('max_working_bytes', 192*n + 64*fft_length + 256*(2*effective_lag+1), 'pair candidates')
    work = 24*fft_length*fft_length.bit_length()
    budget.limits.check('max_work_units', budget.work_units + work, 'pair correlation')
    budget.work_units += work
    values = {key: None for key in DELAY_METRICS}
    evidence = {'schema': 'wavebench.pair_delay.v1', 'parameters': operation, 'searched_lag_samples': max_lag,
                'positive_delay': 'response_later', 'warnings': []}
    def finish():
        return {f"{operation['name']}_{key}": values[key] for key in operation['metrics']}, evidence
    if np.var(x.voltage_v) == 0 or np.var(y.voltage_v) == 0:
        evidence['warnings'].append('zero variance; delay unavailable')
        return finish()
    xv = x.voltage_v - np.mean(x.voltage_v) if operation['remove_mean'] else x.voltage_v
    yv = y.voltage_v - np.mean(y.voltage_v) if operation['remove_mean'] else y.voltage_v
    cross = correlate(yv, xv, mode='full', method='fft')
    ex, ey = np.r_[0., np.cumsum(xv*xv)], np.r_[0., np.cumsum(yv*yv)]
    candidates = []
    for lag in range(-effective_lag, effective_lag+1):
        if lag % 4096 == 0:
            checkpoint()
        overlap = n-abs(lag)
        if overlap < n*operation['min_overlap_ratio']:
            continue
        lx, ly = max(0, -lag), max(0, lag)
        energy = (ex[lx+overlap]-ex[lx])*(ey[ly+overlap]-ey[ly])
        if energy <= 0 or not np.isfinite(energy):
            continue
        coefficient = float(cross[n-1+lag]/np.sqrt(energy))
        if abs(coefficient) > 1 + 1e-9 or not np.isfinite(coefficient):
            raise DataError('invalid normalized correlation')
        coefficient = float(np.clip(coefficient, -1, 1))
        score = abs(coefficient) if operation['polarity'] == 'either' else coefficient
        candidates.append((score, lag, coefficient, overlap))
    budget.limits.check('max_peak_candidates', len(candidates), 'pair delay candidates')
    candidates.sort(reverse=True)
    if not candidates or candidates[0][0] < operation['min_correlation']:
        evidence['warnings'].append('no lag passes correlation/overlap gates')
        return finish()
    best = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    evidence.update(best_score=best[0], second_score=second[0] if second else None)
    if second and best[0] - second[0] <= operation['ambiguity_delta']:
        evidence['warnings'].append('ambiguous correlation peaks; delay unavailable')
        return finish()
    values.update(response_delay_samples=best[1], response_delay_s=float(best[1]*dt), correlation=best[2],
                  polarity=1 if best[2] >= 0 else -1, overlap_samples=best[3])
    return finish()


@dataclass
class PairSpectrum:
    data: np.ndarray
    evidence: dict
    metrics: dict


def transfer_estimate(x, y, op, budget):
    from scipy.signal import get_window, detrend
    from scipy.fft import rfft, rfftfreq
    n, m, f = len(x.time_s), op['nperseg'], op['nfft']
    hop = m-op['noverlap']
    k = 1+(n-m)//hop
    if k < 2:
        raise DataError('pair transfer requires at least two complete Welch segments')
    budget.stage(dict(op='psd', average='mean', nperseg=m, noverlap=op['noverlap'], nfft=f), n*2)
    budget.limits.check('max_working_bytes', 128*n + 256*f + 128*m, 'pair transfer')
    dt = _uniform_sample_interval(x, 'pair transfer')
    window = get_window(op['window'], m, fftbins=True)
    scale = dt/np.sum(window*window)
    sxx, syy, sxy = np.zeros(f//2+1), np.zeros(f//2+1), np.zeros(f//2+1, complex)
    for start in range(0, n-m+1, hop):
        checkpoint()
        a, b = x.voltage_v[start:start+m], y.voltage_v[start:start+m]
        if op['detrend'] != 'none':
            a, b = detrend(a, type=op['detrend']), detrend(b, type=op['detrend'])
        X, Y = rfft(a*window, n=f), rfft(b*window, n=f)
        sxx += np.abs(X)**2
        syy += np.abs(Y)**2
        sxy += np.conj(X)*Y
    factor = np.full(len(sxx), 2*scale/k)
    factor[0] /= 2
    if f % 2 == 0:
        factor[-1] /= 2
    sxx *= factor
    syy *= factor
    sxy *= factor
    if not all(np.all(np.isfinite(a)) for a in (sxx, syy, sxy)):
        raise DataError('pair spectral overflow')
    valid = (sxx >= op['min_reference_density']) & (syy >= op['min_response_density'])
    h = np.full(len(sxx), complex(np.nan, np.nan))
    c = np.full(len(sxx), np.nan)
    h[valid] = sxy[valid]/sxx[valid]
    c[valid] = (np.abs(sxy[valid])/np.sqrt(sxx[valid])/np.sqrt(syy[valid]))**2
    if np.any(c[valid] > 1+1e-9) or not np.all(np.isfinite(c[valid])):
        raise DataError('pair coherence outside rounding tolerance')
    c[valid] = np.clip(c[valid], 0, 1)
    valid &= np.isfinite(h) & (np.abs(h) > 0)
    phase, gain = np.full(len(sxx), np.nan), np.full(len(sxx), np.nan)
    phase[valid], gain[valid] = np.angle(h[valid]), 20*np.log10(np.abs(h[valid]))
    if op['unwrap_phase']:
        indices = np.flatnonzero(valid)
        for part in np.split(indices, np.flatnonzero(np.diff(indices)>1)+1):
            if len(part):
                phase[part] = np.unwrap(phase[part])
    h[~valid], c[~valid] = complex(np.nan, np.nan), np.nan
    coherent = valid & (c >= op['min_coherence'])
    raw_metrics = {'valid_bin_count': int(valid.sum()), 'coherent_bin_count': int(coherent.sum()),
                   'mean_coherence': float(np.mean(c[valid])) if valid.any() else None,
                   'min_coherence': float(np.min(c[valid])) if valid.any() else None}
    evidence = {'schema': 'wavebench.pair_transfer.v1', 'parameters': op, 'segments': k,
                'discarded_tail_samples': (n-m)%hop, 'direction': 'conj(reference_fft)*response_fft',
                'bin_spacing_hz': 1/dt/f, 'coherence_clip_atol': 1e-9,
                'warnings': ['weak excitation bins masked'] if not valid.all() else []}
    if np.any(valid & ~coherent):
        evidence['warnings'].append('low coherence bins retained with quality mask')
    data = np.column_stack((rfftfreq(f, dt), h.real, h.imag, gain, phase, c, valid, coherent))
    return PairSpectrum(data, evidence, {f"{op['name']}_{key}": raw_metrics[key] for key in op['metrics']})
