import numpy as np
import pytest
from scipy import signal as scipy_signal

from wavebench.data.signal_pipeline import filter_fir, filter_iir, validate_waveform, welch_psd
from wavebench.data.analysis_resources import AnalysisBudget, AnalysisLimits, AnalysisResourceError


@pytest.mark.parametrize('window', ['hann', 'hamming', 'blackman'])
@pytest.mark.parametrize('detrend', ['none', 'constant', 'linear'])
@pytest.mark.parametrize('nfft', [127, 256])
def test_segment_mean_matches_full_welch(window, detrend, nfft):
    values = np.random.default_rng(24).normal(size=10003)
    waveform = validate_waveform(np.column_stack((np.arange(len(values)) / 4096, values)))
    params = dict(method='welch', window=window, nperseg=127, noverlap=83,
                  nfft=nfft, detrend=detrend, average='mean')
    actual = welch_psd(waveform, **params)
    frequency, expected = scipy_signal.welch(values, fs=4096,
        window=scipy_signal.get_window(window, 127, fftbins=True), nperseg=127,
        noverlap=83, nfft=nfft, detrend=False if detrend == 'none' else detrend)
    np.testing.assert_array_equal(actual.frequency_hz, frequency)
    np.testing.assert_allclose(actual.psd_v2_per_hz, expected, rtol=2e-13, atol=1e-16)
    assert actual.discarded_tail_samples == (len(values) - 127) % 44


@pytest.mark.parametrize('family', ['fir', 'iir'])
def test_causal_filter_preserves_state_at_blocks(family):
    values = np.random.default_rng(9).normal(size=9001)
    values[4095:4098] += 10
    waveform = validate_waveform(np.column_stack((np.arange(len(values)) / 4096, values)))
    if family == 'fir':
        result = filter_fir(waveform, response='lowpass', cutoff_hz=200, numtaps=101, mode='causal')
        expected = scipy_signal.lfilter(result.taps, [1.0], values)
    else:
        result = filter_iir(waveform, design='butterworth', response='lowpass', cutoff_hz=200, order=5, mode='causal')
        expected = scipy_signal.sosfilt(result.sos, values)
    np.testing.assert_allclose(result.signal.voltage_v, expected, rtol=1e-12, atol=1e-14)
    np.testing.assert_array_equal(waveform.voltage_v, values)


def test_mean_budget_does_not_retain_segment_matrix():
    params = dict(op='psd', nperseg=1024, noverlap=512, nfft=32768, average='mean')
    AnalysisBudget(AnalysisLimits(max_work_units=4_000_000_000)).stage(params, 262144)
    with pytest.raises(AnalysisResourceError, match='max_working_bytes'):
        AnalysisBudget(AnalysisLimits(max_work_units=4_000_000_000)).stage(params | {'average': 'median'}, 262144)
