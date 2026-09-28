from dataclasses import replace
import numpy as np
import pytest

from wavebench.data.signal_pipeline import PsdSignal
from wavebench.data.spectral_quality import normalize_quality, spectral_quality, QUALITY_METRICS
from wavebench.errors import DataError, ConfigError
from wavebench.services.run_plan import normalize_analysis_operations


QUALITY = dict(op='spectral_quality', name='quality', metrics=sorted(QUALITY_METRICS),
    band_hz=[0, 100], dc_exclude_hz=[0, 1], exclude_hz=[], fundamental={'frequency_hz': 10},
    fundamental_half_width_hz=1, harmonic_orders=[2, 3], harmonic_half_width_hz=1,
    min_fundamental_v2=.1, min_peak_density_v2_per_hz=1, min_prominence_v2_per_hz=1,
    min_noise_bins=10, spur_search_hz=[2, 100], spur_half_width_hz=1, spur_distance_hz=3,
    spur_min_density_v2_per_hz=1, spur_min_prominence_v2_per_hz=1)


def spectrum():
    p = np.full(101, .01)
    p[10], p[20], p[30], p[55] = 100, 4, 1, 2
    return PsdSignal(np.arange(101.), p, .005, 400,
                     {'average': 'mean', 'window': 'hann'}, 3, 0, .375, 'hash', 'test')


def test_integral_quality_has_traceable_numerators():
    values, evidence, warnings = spectral_quality(spectrum(), QUALITY)
    pf, ph = 100.02, 5.04
    pn = 2 + (99 - 9 - 1) * .01
    assert values['quality_fundamental_power_v2'] == pytest.approx(pf)
    assert values['quality_harmonic_power_v2'] == pytest.approx(ph)
    assert values['quality_noise_power_v2'] == pytest.approx(pn)
    assert values['quality_snr_db'] == pytest.approx(10*np.log10(pf/pn))
    assert values['quality_sinad_db'] == pytest.approx(10*np.log10(pf/(ph+pn)))
    assert values['quality_sfdr_db'] == pytest.approx(10*np.log10(pf/4.02))
    assert evidence['largest_spur']['overlaps_harmonic']
    assert not warnings


def test_search_threshold_nulls_and_region_rejection():
    signal = spectrum()
    values, _, _ = spectral_quality(signal, QUALITY | {'fundamental': {'search_hz': [5, 15]}})
    assert values['quality_fundamental_frequency_hz'] == 10
    zero = replace(signal, psd_v2_per_hz=np.zeros(101))
    values, _, warnings = spectral_quality(zero, QUALITY)
    assert all(value is None for value in values.values()) and warnings
    with pytest.raises(DataError, match='half-width'):
        spectral_quality(signal, QUALITY | {'fundamental_half_width_hz': .5})
    with pytest.raises(DataError, match='overlap'):
        spectral_quality(signal, QUALITY | {'harmonic_half_width_hz': 9})
    with pytest.raises(DataError, match='mean'):
        spectral_quality(replace(signal, parameters={'average': 'median'}), QUALITY)


def test_zero_noise_and_uncovered_harmonics():
    signal = spectrum()
    p = np.zeros(101)
    p[10] = 100
    values, evidence, warnings = spectral_quality(replace(signal, psd_v2_per_hz=p), QUALITY | {'harmonic_orders': [10]})
    assert values['quality_snr_db'] is None and values['quality_sfdr_db'] is None
    assert evidence['harmonics'][0]['covered'] is False and warnings


def test_unresolved_spurs_are_unavailable():
    signal = spectrum()
    signal.psd_v2_per_hz[57] = 3
    values, _, warnings = spectral_quality(signal, QUALITY)
    assert values['quality_sfdr_db'] is None and any('unresolved' in warning for warning in warnings)


@pytest.mark.parametrize('change', [{'fundamental': {}}, {'harmonic_orders': [2, 2]}, {'min_noise_bins': True},
                                    {'extra': 1}, {'metrics': ['snr_db', 'snr_db']}])
def test_quality_schema_rejects_ambiguous_inputs(change):
    with pytest.raises(DataError):
        normalize_quality(QUALITY | change)


def test_runplan_domain_and_expect_contract():
    psd = dict(op='psd', method='welch', window='hann', nperseg=32, noverlap=16, nfft=32, detrend='none', average='mean')
    fields = {'operations': [psd, QUALITY], 'expect': {'quality_snr_db': {'min': 10}}}
    normalize_analysis_operations('test', fields)
    with pytest.raises(ConfigError):
        normalize_analysis_operations('test', {'operations': [psd | {'average': 'median'}, QUALITY]})
    with pytest.raises(ConfigError):
        normalize_analysis_operations('test', {'operations': [QUALITY]})
