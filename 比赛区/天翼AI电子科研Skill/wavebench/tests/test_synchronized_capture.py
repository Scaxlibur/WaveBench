import json
from pathlib import Path
import pytest
from wavebench.errors import ConfigError, DataError
from wavebench.data.analysis_resources import AnalysisLimits
from wavebench.services.run_plan import load_run_plan
from wavebench.services.pair_service import load_pair_source, execute_pair_step
from wavebench.services.run_artifacts import RunStepRecord
from wavebench.services.execution_intent import build_execution_intent
from test_pair_analysis import pair_package
from test_run_service import make_config


def driver_evidence(capture):
    path=capture/'metadata.json'
    metadata=json.loads(path.read_text())
    evidence=metadata['synchronization']
    evidence.update(kind='driver_frozen_single', producer={'name':'example.scope','version':'single-stop.v1'},
                    driver={'id':'example.scope','model':'EXAMPLE','firmware':'1.0'})
    evidence['acquisition_group']['source']='driver_frozen_single'
    evidence['procedure']={'contract':'single-stop.v1','single_count':1,'single_opc':True,
        'stop_opc_before':True,'stop_opc_after':True,
        'reads':[{'channel':ch,'configuration_unchanged':True} for ch in (1,2)],
        'configuration':{'mode':'single'}}
    for item in evidence['channels'].values():
        item.update(skew_s=None,uncertainty_s=None)
    metadata['instrument']={'idn':'Example,EXAMPLE,REDACTED,1.0'}
    path.write_text(json.dumps(metadata))
    return metadata


def plan_file(tmp_path):
    path=tmp_path/'plan.toml'
    path.write_text('''[[steps]]
id="capture"
kind="scope.capture"
channels=[1,2]
synchronized=true
save_npy=true
[[steps]]
id="pair"
kind="analysis.pair"
source={step="capture"}
reference_channel=1
response_channel=2
operations=[{op="delay",name="t",max_lag_s=0.001,remove_mean=true,polarity="same",min_overlap_ratio=0.9,min_correlation=0.8,ambiguity_delta=0.01,metrics=["response_delay_samples"]}]
''')
    return path


def test_sync_plan_and_intent(tmp_path):
    plan=load_run_plan(plan_file(tmp_path))
    assert plan.steps[0].fields['points']=='DEF'
    intent=build_execution_intent(plan,make_config(str(tmp_path)))
    assert intent.operations[0]['operation']=='scope.capture_synchronized'
    assert intent.operations[0]['effect']=='acquire'
    from wavebench.services.run_safety import plan_scope_guard_channels
    assert plan_scope_guard_channels(plan,1)==[1,2]


@pytest.mark.parametrize('text,replacement', [('channels=[1,2]','channels=[1,1]'),
    ('synchronized=true','synchronized="true"'),('save_npy=true','save_npy=false'),
    ('synchronized=true','synchronized=true\nchannel=1'),('synchronized=true','synchronized=true\npoints=42'),
    ('synchronized=true','synchronized=true\nauto_recover=true')])
def test_sync_plan_rejects_ambiguous_or_retry_config(tmp_path,text,replacement):
    path=plan_file(tmp_path)
    path.write_text(path.read_text().replace(text,replacement))
    with pytest.raises(ConfigError):
        load_run_plan(path)


def test_driver_proof_unknown_skew_and_identity_match(tmp_path):
    capture,_=pair_package(tmp_path)
    metadata=driver_evidence(capture)
    fields={'reference_channel':1,'response_channel':2}
    source,_,_=load_pair_source(capture,fields,AnalysisLimits())
    assert source['axes']['evidence_kind']=='driver_frozen_single'
    metadata['instrument']['idn']='Example,DIFFERENT,REDACTED,1.0'
    (capture/'metadata.json').write_text(json.dumps(metadata))
    with pytest.raises(DataError,match='identity'):
        load_pair_source(capture,fields,AnalysisLimits())


@pytest.mark.parametrize('field', ['single_opc','stop_opc_before','stop_opc_after','reads','single_count'])
def test_incomplete_driver_proof_rejected(tmp_path,field):
    capture,_=pair_package(tmp_path)
    metadata=driver_evidence(capture)
    metadata['synchronization']['procedure'].pop(field)
    (capture/'metadata.json').write_text(json.dumps(metadata))
    with pytest.raises(DataError,match='proof'):
        load_pair_source(capture,{'reference_channel':1,'response_channel':2},AnalysisLimits())


def test_pair_step_uses_legacy_cwd_relative_capture_path(tmp_path,monkeypatch):
    capture,_=pair_package(tmp_path)
    driver_evidence(capture)
    monkeypatch.chdir(tmp_path)
    plan=load_run_plan(plan_file(tmp_path))
    run_dir=tmp_path/'runs'/'example'
    run_dir.mkdir(parents=True)
    record=RunStepRecord(index=0,kind='scope.capture',status='ok',fields={},artifact={'package':'capture'})
    result=execute_pair_step(run_dir=run_dir,step=plan.steps[1],source_step=plan.steps[0],source_record=record)
    assert result['analysis_pipeline']['status']=='ok'
    assert result['metrics']['t_response_delay_samples']==0
    assert Path(result['analysis_pipeline']['manifest']).is_absolute() is False


@pytest.mark.parametrize('valid', [True,False])
def test_service_persists_proof_only_after_validation(tmp_path,monkeypatch,valid):
    from contextlib import contextmanager
    from types import SimpleNamespace
    import numpy as np
    from wavebench.instruments.models import WaveformData,WaveformHeader
    from wavebench.instruments.synchronized_capture import SynchronizedCapture
    from wavebench.services.scope_service import ScopeService
    from wavebench.logging import CommandLogger
    capture,_=pair_package(tmp_path)
    metadata=driver_evidence(capture)
    proof=metadata['synchronization']
    waves={ch:WaveformData(ch,WaveformHeader(0,2047/1024,2048),np.ones(2048)) for ch in (1,2)}
    if not valid:
        proof['procedure']['single_opc']=False
    def acquire(**kwargs):
        for ch,waveform in waves.items():
            kwargs['on_waveform'](ch,waveform)
        return SynchronizedCapture(waves,proof)
    fake=SimpleNamespace(idn=lambda:'Example,EXAMPLE,REDACTED,1.0',capture_synchronized=acquire)
    @contextmanager
    def session():
        yield fake
    config=make_config(str(tmp_path)).with_waveform_overrides(points='DEF').with_output_overrides(save_npy=True,save_screenshot=False)
    service=ScopeService(config,CommandLogger())
    monkeypatch.setattr(service,'_scope_session',session)
    monkeypatch.setattr(service,'_require',lambda *args:None)
    monkeypatch.setattr(service,'_waveform_binary_profile',lambda:None)
    monkeypatch.setattr(service,'_session_preflight',lambda *args:{'scope.identity':fake.idn()})
    if valid:
        result=service.capture_waveforms([1,2],'sync',synchronized=True)
        saved=json.loads(result.metadata_path.read_text())
        assert saved['synchronization']['kind']=='driver_frozen_single'
    else:
        with pytest.raises(DataError,match='proof'):
            service.capture_waveforms([1,2],'sync',synchronized=True)
        for path in Path(config.output.directory).glob('*/metadata.json'):
            assert 'synchronization' not in json.loads(path.read_text())
