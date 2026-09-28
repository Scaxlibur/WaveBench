import json
from unittest.mock import patch
import pytest

from wavebench.services.analysis_batch import run_batch
from wavebench.errors import ConfigError
from test_analysis_service import analysis_input as analysis_input


def batch_file(tmp_path, capture, recipe, *, duplicates='reject', second=False, cap=1000000):
    manifest = tmp_path / 'batch.toml'
    text = f'''schema="wavebench.analysis_batch.v1"
recipe={json.dumps(str(recipe))}
on_failure="continue"
duplicates="{duplicates}"
max_output_bytes={cap}
[[entries]]
id="first"
capture={json.dumps(str(capture))}
channel=1
'''
    if second:
        text += f'[[entries]]\nid="second"\ncapture={json.dumps(str(capture))}\nchannel=1\n'
    manifest.write_text(text)
    return manifest


def test_batch_success_verified_resume_and_tamper(tmp_path, analysis_input):
    capture, recipe = analysis_input
    manifest = batch_file(tmp_path, capture, recipe)
    output = tmp_path / 'results'
    result = run_batch(manifest, output)
    assert result['status'] == 'ok'
    assert result['entries'][0]['metrics']['voltage_mean_v'] == 1
    with patch('wavebench.services.analysis_batch.run_analysis', side_effect=AssertionError('reprocessed')):
        resumed = run_batch(manifest, output, resume=True)
    assert resumed['status'] == 'ok'
    (output / result['entries'][0]['directory'] / 'metrics.json').write_text('{}')
    with pytest.raises(ConfigError, match='artifacts changed'):
        run_batch(manifest, output, resume=True)


def test_batch_source_change_rejects_resume(tmp_path, analysis_input):
    capture, recipe = analysis_input
    manifest = batch_file(tmp_path, capture, recipe)
    output = tmp_path / 'results'
    run_batch(manifest, output)
    (capture / 'metadata.json').write_text((capture / 'metadata.json').read_text() + ' ')
    with pytest.raises(ConfigError, match='changed'):
        run_batch(manifest, output, resume=True)


def test_batch_duplicate_and_output_budget(tmp_path, analysis_input):
    capture, recipe = analysis_input
    manifest = batch_file(tmp_path, capture, recipe, second=True)
    with pytest.raises(ConfigError, match='duplicate'):
        run_batch(manifest, tmp_path / 'rejected')
    manifest = batch_file(tmp_path, capture, recipe, second=True, duplicates='allow', cap=100)
    result = run_batch(manifest, tmp_path / 'small')
    assert result['status'] == 'failed'
    assert all(item['status'] == 'failed' for item in result['entries'])


def test_batch_cancel_then_resume(tmp_path, analysis_input):
    import threading
    capture, recipe = analysis_input
    manifest = batch_file(tmp_path, capture, recipe)
    event = threading.Event()
    event.set()
    output = tmp_path / 'results'
    first = run_batch(manifest, output, cancel_event=event)
    assert first['cancelled'] and first['status'] == 'failed'
    result = run_batch(manifest, output, resume=True)
    assert result['status'] == 'ok' and not result.get('cancelled')


def test_batch_recipe_resources_are_tightened_by_aggregate(tmp_path, analysis_input):
    capture, recipe = analysis_input
    recipe.write_text(recipe.read_text()+'\n[resources]\nmax_output_bytes=1000000\n')
    manifest = batch_file(tmp_path, capture, recipe)
    result=run_batch(manifest,tmp_path/'results')
    assert result['status']=='ok'


def test_batch_failed_attempt_is_preserved_and_retried(tmp_path, analysis_input):
    capture,recipe=analysis_input
    recipe.write_text(recipe.read_text().replace('min=0.9','min=1.1'))
    manifest=batch_file(tmp_path,capture,recipe)
    output=tmp_path/'results'
    first=run_batch(manifest,output)
    assert first['status']=='failed'
    second=run_batch(manifest,output,resume=True)
    assert second['status']=='failed' and len(second['entries'][0]['attempts'])==2
    assert (output/'items/first/attempt_001/analysis.json').exists()
    assert (output/'items/first/attempt_002/analysis.json').exists()


def test_batch_continue_source_failure_and_report(tmp_path, analysis_input):
    from wavebench.report.analysis import write_analysis_report
    capture,recipe=analysis_input
    manifest=batch_file(tmp_path,capture,recipe,second=True,duplicates='allow')
    text=manifest.read_text().replace('id="first"','id="first"').replace('channel=1','channel=99',1)
    manifest.write_text(text)
    result=run_batch(manifest,tmp_path/'results')
    assert [item['status'] for item in result['entries']]==['failed','ok']
    html=write_analysis_report([tmp_path/'results'],tmp_path/'report.html').read_text()
    assert 'polyline' in html


def test_inventory_never_reads_active_lock(tmp_path, monkeypatch):
    from wavebench.services import analysis_batch
    from wavebench.data.analysis_resources import AnalysisLimits
    (tmp_path / '.batch.lock').write_bytes(b'locked')
    artifact = tmp_path / 'result.json'
    artifact.write_text('{}')
    original = analysis_batch._sha256_file
    def hash_file(path):
        assert path.name != '.batch.lock', 'cannot read a Windows locked file'
        return original(path)
    monkeypatch.setattr(analysis_batch, '_sha256_file', hash_file)
    assert set(analysis_batch._inventory(tmp_path, AnalysisLimits())) == {'result.json'}
