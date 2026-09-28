from dataclasses import replace
import json
import numpy as np
import pytest
from scipy.signal import csd, welch, get_window

from wavebench.data.analysis_resources import AnalysisBudget, AnalysisLimits
from wavebench.data.signal_pipeline import TimeSignal
from wavebench.data.pair_analysis import delay_estimate, transfer_estimate, validate_sync
from wavebench.errors import DataError, ConfigError
from wavebench.services.pair_service import pair_run, pair_check
from wavebench.services.analysis_execution import AnalysisExecution


DELAY = dict(op='delay', name='timing', max_lag_s=.05, remove_mean=True, polarity='either',
             min_overlap_ratio=.8, min_correlation=.8, ambiguity_delta=.01,
             metrics=['response_delay_samples', 'response_delay_s', 'correlation', 'polarity'])
TRANSFER = dict(op='transfer', name='system', window='hann', nperseg=128, noverlap=64, nfft=256,
                detrend='constant', min_reference_density=1e-15, min_response_density=1e-15,
                min_coherence=.8, unwrap_phase=True, metrics=['mean_coherence', 'min_coherence', 'valid_bin_count'])


def sync_evidence(n, dt):
    return {'schema': 'wavebench.capture_sync.v1', 'kind': 'synthetic', 'status': 'verified',
        'producer': {'name': 'test-generator', 'version': '1'},
        'acquisition_group': {'id': 'synthetic-test', 'source': 'synthetic'},
        'timebase_id': 'shared-clock', 'record_id': 'record-1',
        'guarantees': {'single_record': True, 'frozen_read': True},
        'channels': {str(ch): {'time_start_s': 0., 'sample_interval_s': dt, 'samples': n,
                               'skew_s': 0., 'uncertainty_s': 0.} for ch in (1, 2)}}


def pair_package(tmp_path, n=2048):
    capture = tmp_path/'capture'
    capture.mkdir()
    x = np.random.default_rng(17).normal(size=n)
    t = np.arange(n)/1024
    np.save(capture/'ch1.npy', np.column_stack((t, x)))
    np.save(capture/'ch2.npy', np.column_stack((t, 2*x)))
    metadata = {'channels': {'1': {}, '2': {}}, 'files': {'1': {'npy': 'ch1.npy'}, '2': {'npy': 'ch2.npy'}},
                'synchronization': sync_evidence(n, 1/1024)}
    (capture/'metadata.json').write_text(json.dumps(metadata))
    operations = [DELAY, TRANSFER, {'op': 'export', 'name': 'transfer', 'formats': ['npy','csv']}]
    def table(op):
        return '{'+', '.join(f'{key}={json.dumps(value)}' for key,value in op.items())+'}'
    recipe = tmp_path/'pair.toml'
    recipe.write_text('schema="wavebench.analysis_pair_recipe.v1"\nreference_channel=1\nresponse_channel=2\noperations=['
                      + ','.join(table(op) for op in operations)+']\n[expect]\nsystem_mean_coherence={min=0.99}\n')
    return capture, recipe


@pytest.mark.parametrize('lag,sign', [(8,1),(-7,1),(9,-1)])
def test_delay_sign_and_inversion(lag, sign):
    rng = np.random.default_rng(14)
    x = rng.normal(size=2048)
    y = np.zeros_like(x)
    if lag > 0:
        y[lag:] = x[:-lag]*sign
    else:
        y[:lag] = x[-lag:]*sign
    t = np.arange(len(x))/1024
    values, _ = delay_estimate(TimeSignal(t,x), TimeSignal(t,y), DELAY, AnalysisBudget(AnalysisLimits()))
    assert values['timing_response_delay_samples'] == lag
    assert values['timing_polarity'] == sign
    assert values['timing_response_delay_s'] == lag/1024


def test_periodic_and_zero_delay_unavailable():
    t = np.arange(2048)/1024
    x = np.sin(2*np.pi*64*t)
    for samples in (x, np.zeros_like(x)):
        metrics, evidence = delay_estimate(TimeSignal(t,samples), TimeSignal(t,samples), DELAY, AnalysisBudget(AnalysisLimits()))
        assert metrics['timing_response_delay_s'] is None and evidence['warnings']


@pytest.mark.parametrize('nfft', [255,256])
def test_transfer_matches_independent_scipy_reference(nfft):
    x = np.random.default_rng(2).normal(size=2048)
    y = 2*np.roll(x,3) + .1*np.random.default_rng(3).normal(size=2048)
    t = np.arange(len(x))/1024
    op = TRANSFER | {'nfft':nfft}
    result = transfer_estimate(TimeSignal(t,x), TimeSignal(t,y), op, AnalysisBudget(AnalysisLimits()))
    kwargs = dict(fs=1024, window=get_window('hann',128), nperseg=128, noverlap=64,nfft=nfft,detrend='constant')
    f,sxy = csd(x,y,**kwargs)
    _,sxx = welch(x,**kwargs)
    _,syy = welch(y,**kwargs)
    h = result.data[:,1] + 1j*result.data[:,2]
    np.testing.assert_allclose(h,sxy/sxx,rtol=1e-12,atol=1e-12)
    np.testing.assert_allclose(result.data[:,5],abs(sxy)**2/(sxx*syy),rtol=1e-12,atol=1e-12)
    np.testing.assert_array_equal(result.data[:,0], f)


def test_sync_requires_evidence_and_dt_scaled_axis_match():
    x = TimeSignal(np.arange(100)/1024, np.ones(100))
    with pytest.raises(DataError, match='synchronization evidence'):
        validate_sync(x,x,{},(1,2))
    evidence = sync_evidence(100,1/1024)
    with pytest.raises(DataError, match='synthetic'):
        validate_sync(x,x,evidence | {'kind':'driver'},(1,2))
    shifted = replace(x,time_s=x.time_s+1e-5)
    with pytest.raises(DataError):
        validate_sync(x,shifted,evidence,(1,2))
    assert validate_sync(x,x,evidence,(1,2))['evidence_kind'] == 'synthetic'


def test_pair_source_export_report_and_spawn(tmp_path):
    from wavebench.report.analysis import write_analysis_report
    capture,recipe = pair_package(tmp_path)
    original = (capture/'ch1.npy').read_bytes()
    assert pair_check(capture,recipe)['status']=='ok'
    result = pair_run(capture,recipe,tmp_path/'result',execution_policy=AnalysisExecution())
    assert result['status']=='ok'
    assert result['artifact']['metrics']['timing_response_delay_samples']==0
    array=np.load(tmp_path/'result/exports/transfer.npy')
    np.testing.assert_allclose(array[:,3],20*np.log10(2),atol=1e-12)
    html=write_analysis_report([tmp_path/'result'],tmp_path/'report.html').read_text()
    assert 'Pair analysis' in html and 'Gain (dB)' in html and 'Coherence' in html
    assert (capture/'ch1.npy').read_bytes()==original


def test_pair_invalid_masks_and_source_gate(tmp_path):
    capture,recipe=pair_package(tmp_path)
    raw=np.load(capture/'ch1.npy')
    raw[:,1]=0
    np.save(capture/'ch2.npy',raw)
    result=pair_run(capture,recipe,tmp_path/'masked')
    assert result['status']=='failed'  # expectation unavailable
    array=np.load(tmp_path/'masked/exports/transfer.npy')
    assert not array[:,6:].any() and np.isnan(array[:,1:6]).all()
    text=(tmp_path/'masked/exports/transfer.csv').read_text()
    assert ',,,,,,' in text
    metadata=json.loads((capture/'metadata.json').read_text())
    metadata.pop('synchronization')
    (capture/'metadata.json').write_text(json.dumps(metadata))
    with pytest.raises(DataError, match='synchronization'):
        pair_check(capture,recipe)


def test_pair_runplan_schema_and_offline_intent(tmp_path):
    from wavebench.services.run_plan import load_run_plan
    from wavebench.services.execution_intent import build_execution_intent
    from test_run_service import make_config
    _, recipe = pair_package(tmp_path)
    from wavebench.services.pair_service import load_pair_recipe
    fields=load_pair_recipe(recipe,AnalysisLimits())
    operations='['+','.join('{'+','.join(f'{k}={json.dumps(v)}' for k,v in op.items())+'}' for op in fields['operations'])+']'
    path=tmp_path/'plan.toml'
    path.write_text('[[steps]]\nid="capture"\nkind="scope.capture"\nsave_npy=true\n[[steps]]\nkind="analysis.pair"\n'
                    'source={step="capture"}\nreference_channel=1\nresponse_channel=2\noperations='+operations)
    plan=load_run_plan(path)
    intent=build_execution_intent(plan,make_config(str(tmp_path)))
    assert intent.operations[-1]['effect']=='offline' and intent.operations[-1]['lease_mode']=='none'
    path.write_text(path.read_text()+'\nsafety_gate={}\n')
    with pytest.raises(ConfigError):
        load_run_plan(path)


def test_pair_runplan_executes_after_hardware_close(tmp_path):
    from test_run_service_analysis import PhaseRunService
    from test_run_service import make_config
    from wavebench.logging import CommandLogger
    from wavebench.services.run_artifacts import RunStepRecord
    from wavebench.services.run_plan import load_run_plan
    from wavebench.services.pair_service import load_pair_recipe
    capture,recipe=pair_package(tmp_path)
    fields=load_pair_recipe(recipe,AnalysisLimits())
    operations='['+','.join('{'+','.join(f'{k}={json.dumps(v)}' for k,v in op.items())+'}' for op in fields['operations'])+']'
    path=tmp_path/'plan.toml'
    path.write_text('[[steps]]\nid="capture_main"\nkind="scope.capture"\nsave_npy=true\n[[steps]]\nid="pair_main"\nkind="analysis.pair"\n'
                    'source={step="capture_main"}\nreference_channel=1\nresponse_channel=2\noperations='+operations)
    record=RunStepRecord(index=0,kind='scope.capture',status='ok',fields={},artifact={'package':str(capture),'metadata':str(capture/'metadata.json')})
    events=[]
    service=PhaseRunService(config=make_config(str(tmp_path)),logger=CommandLogger(),capture_record=record,events=events,
                            analysis_execution=AnalysisExecution())
    from unittest.mock import patch
    from wavebench.services.pair_service import execute_pair_step
    def checked(**kwargs):
        assert events[-1]=='session_and_lease_closed'
        return execute_pair_step(**kwargs)
    with patch('wavebench.services.pair_service.execute_pair_step',side_effect=checked):
        result=service.run(load_run_plan(path))
    assert result.steps[-1].status=='ok'
    from wavebench.data.packages import load_run_package
    from wavebench.report.html import write_run_report_html
    html=write_run_report_html(load_run_package(result.run_dir)).read_text()
    assert 'Pair analysis' in html and 'Gain (dB)' in html


def test_pair_supervised_cancel_preserves_terminal_state(tmp_path):
    import threading
    capture, recipe = pair_package(tmp_path)
    event = threading.Event()
    event.set()
    result = pair_run(capture, recipe, tmp_path/'cancelled', execution_policy=AnalysisExecution(), cancel_event=event)
    assert result['status'] == 'failed'
    assert result['artifact']['analysis_pipeline']['error']['code'] == 'analysis_cancelled'
    assert (tmp_path/'cancelled/manifest.json').exists()


def test_pair_zero_segment_and_resource_preflight(tmp_path):
    capture, recipe = pair_package(tmp_path, n=128)
    with pytest.raises(DataError, match='two complete'):
        pair_check(capture, recipe)
    with pytest.raises(DataError, match='max_fft_length'):
        pair_check(capture, recipe, resource_limits=AnalysisLimits(max_fft_length=64))
