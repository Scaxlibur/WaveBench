"""Bandwidth-limited PSD integral estimates; never instrument-standard automatic SNR."""
import numpy as np

from wavebench.data.analysis_control import checkpoint
from wavebench.data.pipeline_operations import integer, interval, number, result_name
from wavebench.errors import DataError


QUALITY_METRICS = {'snr_db', 'sinad_db', 'sfdr_db', 'thdn_ratio', 'fundamental_frequency_hz',
                   'fundamental_power_v2', 'harmonic_power_v2', 'noise_power_v2',
                   'noise_bandwidth_hz', 'spur_frequency_hz', 'spur_power_v2', 'spur_dbc'}
QUALITY_FIELDS = {'op', 'name', 'metrics', 'band_hz', 'dc_exclude_hz', 'exclude_hz', 'fundamental',
                  'fundamental_half_width_hz', 'harmonic_orders', 'harmonic_half_width_hz',
                  'min_fundamental_v2', 'min_peak_density_v2_per_hz', 'min_prominence_v2_per_hz',
                  'min_noise_bins', 'spur_search_hz', 'spur_half_width_hz', 'spur_distance_hz',
                  'spur_min_density_v2_per_hz', 'spur_min_prominence_v2_per_hz'}


def normalize_quality(raw):
    if not isinstance(raw, dict) or set(raw) != QUALITY_FIELDS:
        raise DataError('spectral_quality requires exactly the documented fields')
    out = dict(raw, name=result_name(raw['name']))
    metrics = raw['metrics']
    if (not isinstance(metrics, list) or not metrics or any(not isinstance(m, str) or m not in QUALITY_METRICS for m in metrics)
            or len(set(metrics)) != len(metrics)):
        raise DataError('spectral_quality metrics must be distinct documented names')
    for key in ('band_hz', 'dc_exclude_hz', 'spur_search_hz'):
        out[key] = interval(raw[key], key)
    if out['dc_exclude_hz'][0] != 0:
        raise DataError('dc_exclude_hz must start at zero')
    if not isinstance(raw['exclude_hz'], list) or len(raw['exclude_hz']) > 32:
        raise DataError('exclude_hz accepts at most 32 intervals')
    out['exclude_hz'] = [interval(item, 'exclude_hz') for item in raw['exclude_hz']]
    if any(lo < out['band_hz'][0] or hi > out['band_hz'][1] for lo, hi in out['exclude_hz'] + [out['spur_search_hz']]):
        raise DataError('exclusions and spur search must lie inside band_hz')
    fundamental = raw['fundamental']
    if not isinstance(fundamental, dict) or set(fundamental) not in ({'frequency_hz'}, {'search_hz'}):
        raise DataError('fundamental selects frequency_hz or search_hz exclusively')
    out['fundamental'] = ({'frequency_hz': number(fundamental['frequency_hz'], 'frequency_hz')}
                          if 'frequency_hz' in fundamental else {'search_hz': interval(fundamental['search_hz'], 'search_hz')})
    for key in ('fundamental_half_width_hz', 'harmonic_half_width_hz', 'min_fundamental_v2',
                'min_peak_density_v2_per_hz', 'min_prominence_v2_per_hz', 'spur_half_width_hz',
                'spur_distance_hz', 'spur_min_density_v2_per_hz', 'spur_min_prominence_v2_per_hz'):
        out[key] = number(raw[key], key)
        if out[key] <= 0:
            raise DataError(f'{key} must be positive')
    orders = raw['harmonic_orders']
    if not isinstance(orders, list) or len(orders) > 15:
        raise DataError('harmonic_orders must select at most 15 distinct orders')
    out['harmonic_orders'] = [integer(order, 'harmonic order', 2, 16) for order in orders]
    if len(set(out['harmonic_orders'])) != len(orders):
        raise DataError('harmonic_orders must be unique')
    out['min_noise_bins'] = integer(raw['min_noise_bins'], 'min_noise_bins', 1, 1000000)
    return out


def spectral_quality(signal, operation):
    from scipy.signal import find_peaks
    op = normalize_quality(operation)
    if signal.parameters['average'] != 'mean':
        raise DataError('spectral_quality requires mean Welch PSD')
    f, p = signal.frequency_hz, signal.psd_v2_per_hz
    df = float(f[1] - f[0])
    nyquist = .5 / signal.sample_interval_s
    if op['band_hz'][1] > nyquist * (1 + 1e-12):
        raise DataError('spectral_quality band exceeds Nyquist')
    if min(op['fundamental_half_width_hz'], op['harmonic_half_width_hz'], op['spur_half_width_hz']) < df:
        raise DataError('spectral_quality integration half-width must cover at least one bin interval')
    if not np.all(np.isfinite(p)) or np.any(p < 0):
        raise DataError('spectral_quality requires finite nonnegative density')
    def mask(bounds):
        return (f >= bounds[0]) & (f <= bounds[1])
    base = mask(op['band_hz'])
    for bounds in [op['dc_exclude_hz'], *op['exclude_hz']]:
        base &= ~mask(bounds)
    def region(center, width):
        lo, hi = center - width, center + width
        selected = mask([lo, hi])
        if lo < op['band_hz'][0] or hi > min(op['band_hz'][1], nyquist) or not selected.any() or np.any(selected & ~base):
            raise DataError('integration region is clipped by band, DC or excluded intervals')
        return selected
    def power(selected):
        value = float(np.sum(p[selected]) * df)
        if not np.isfinite(value):
            raise DataError('spectral integral overflow')
        return value
    def describe(selected):
        indices = np.flatnonzero(selected)
        # Ranges, not an unbounded list of every bin.
        boundaries = np.flatnonzero(np.diff(indices) > 1) + 1
        ranges = [[int(part[0]), int(part[-1])] for part in np.split(indices, boundaries) if len(part)]
        return {'bin_ranges': ranges, 'bin_count': len(indices), 'bandwidth_hz': len(indices) * df,
                'power_v2': power(selected)}
    values = {key: None for key in QUALITY_METRICS}
    warnings = []
    evidence = {'schema': 'wavebench.spectral_quality.v1', 'parameters': op, 'psd': signal.parameters,
                'bin_spacing_hz': df, 'definition': 'band_limited_psd_integral_no_signal_noise_subtraction',
                'regions': {}, 'harmonics': [], 'reasons': warnings}
    def finish():
        for key, value in values.items():
            if value is not None and not np.isfinite(value):
                values[key] = None
                warnings.append(f'{key}: non-finite result unavailable')
        return {f"{op['name']}_{key}": values[key] for key in op['metrics']}, evidence, warnings
    indices, _ = find_peaks(p, height=op['min_peak_density_v2_per_hz'], prominence=op['min_prominence_v2_per_hz'])
    fundamental = op['fundamental']
    if 'search_hz' in fundamental:
        if fundamental['search_hz'][0] < op['band_hz'][0] or fundamental['search_hz'][1] > op['band_hz'][1]:
            raise DataError('fundamental search must lie inside band_hz')
        selected = [int(i) for i in indices if base[i] and fundamental['search_hz'][0] <= f[i] <= fundamental['search_hz'][1]]
        if not selected:
            warnings.append('no fundamental peak passes explicit density and prominence thresholds')
            return finish()
        peak = max(selected, key=lambda i: (p[i], -i))
        frequency = float(f[peak])
    else:
        frequency = fundamental['frequency_hz']
        peak = int(np.argmin(np.abs(f - frequency)))
        if peak not in indices:
            warnings.append('fixed fundamental bin fails density or prominence threshold')
            return finish()
    F = region(frequency, op['fundamental_half_width_hz'])
    pf = power(F)
    if pf < op['min_fundamental_v2']:
        warnings.append('fundamental integral is below min_fundamental_v2')
        return finish()
    H = np.zeros(len(f), bool)
    for order in op['harmonic_orders']:
        checkpoint()
        center, width = frequency * order, op['harmonic_half_width_hz']
        if center - width < op['band_hz'][0] or center + width > min(op['band_hz'][1], nyquist):
            evidence['harmonics'].append({'order': order, 'covered': False, 'power_v2': None})
            warnings.append(f'harmonic {order} not fully covered')
            continue
        current = region(center, width)
        if np.any(current & (F | H)):
            raise DataError('fundamental and harmonic integration bins overlap')
        H |= current
        evidence['harmonics'].append({'order': order, 'covered': True, **describe(current)})
    N = base & ~(F | H)
    ph, pn = power(H), power(N)
    evidence['regions'] = {'fundamental': describe(F), 'harmonics': describe(H), 'noise': describe(N)}
    values.update(fundamental_frequency_hz=frequency, fundamental_power_v2=pf, harmonic_power_v2=ph,
                  noise_power_v2=pn, noise_bandwidth_hz=float(np.count_nonzero(N) * df))
    enough_noise = np.count_nonzero(N) >= op['min_noise_bins']
    if enough_noise and pn > 0:
        values['snr_db'] = float(10 * (np.log10(pf) - np.log10(pn)))
    else:
        warnings.append('noise denominator unavailable: too few bins or zero power')
    if enough_noise and ph + pn > 0:
        values['sinad_db'] = float(10 * (np.log10(pf) - np.log10(ph + pn)))
        ratio = float(np.sqrt((ph + pn) / pf))
        values['thdn_ratio'] = ratio if np.isfinite(ratio) else None
    else:
        warnings.append('SINAD denominator unavailable')
    spur_indices, _ = find_peaks(p, height=op['spur_min_density_v2_per_hz'], prominence=op['spur_min_prominence_v2_per_hz'])
    search = mask(op['spur_search_hz']) & base & ~F
    candidates, ambiguous, previous = [], False, None
    for i in spur_indices:
        if not search[i]:
            continue
        checkpoint()
        lo, hi = float(f[i]) - op['spur_half_width_hz'], float(f[i]) + op['spur_half_width_hz']
        current = mask([lo, hi])
        if lo < op['spur_search_hz'][0] or hi > op['spur_search_hz'][1] or np.any(current & ~search):
            ambiguous = True
            continue
        if previous is not None and (lo <= previous + op['spur_half_width_hz'] or f[i] - previous < op['spur_distance_hz']):
            ambiguous = True
        previous = float(f[i])
        candidates.append((power(current), int(i), bool(np.any(current & H))))
    if ambiguous:
        warnings.append('spur windows clipped or unresolved; SFDR unavailable')
    elif candidates:
        ps, index, harmonic = max(candidates, key=lambda item: (item[0], -item[1]))
        if ps > 0:
            values.update(spur_frequency_hz=float(f[index]), spur_power_v2=ps,
                          sfdr_db=float(10 * (np.log10(pf) - np.log10(ps))),
                          spur_dbc=float(10 * (np.log10(ps) - np.log10(pf))))
            evidence['largest_spur'] = {'frequency_hz': float(f[index]), 'power_v2': ps, 'overlaps_harmonic': harmonic}
    else:
        warnings.append('no spur passes explicit detection thresholds; SFDR unavailable')
    return finish()
