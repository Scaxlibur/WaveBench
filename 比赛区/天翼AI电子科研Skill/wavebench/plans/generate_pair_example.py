"""Generate a labeled synthetic capture and batch manifest; never access instruments."""
import argparse
import json
from pathlib import Path

import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New output directory')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    n, rate = 16384, 16384
    times = np.arange(n)/rate
    rng = np.random.default_rng(2026)
    reference = rng.normal(size=n)
    response = np.zeros(n)
    response[7:] = 2*reference[:-7]
    for channel, voltage in ((1, reference), (2, response)):
        np.save(root/f'ch{channel}.npy', np.column_stack((times,voltage)))
    metadata = {'channels': {'1': {}, '2': {}},
        'files': {'1': {'npy':'ch1.npy'}, '2': {'npy':'ch2.npy'}},
        'synchronization': {'schema':'wavebench.capture_sync.v1', 'kind':'synthetic', 'status':'verified',
            'producer': {'name':'wavebench-example-generator', 'version':'1'},
            'acquisition_group': {'id':'seed-2026-pair', 'source':'synthetic'},
            'timebase_id':'synthetic-16384hz', 'record_id':'one-record',
            'guarantees': {'single_record':True, 'frozen_read':True},
            'channels': {str(ch): {'time_start_s':0., 'sample_interval_s':1/rate, 'samples':n,
                                  'skew_s':0., 'uncertainty_s':0.} for ch in (1,2)}}}
    (root/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    # A separate single-tone capture illustrates PSD quality estimates.
    tone = root/'tone'
    tone.mkdir()
    voltage = np.sin(2*np.pi*1000*times)+.03*np.sin(2*np.pi*2000*times)+.002*rng.normal(size=n)
    np.save(tone/'ch1.npy',np.column_stack((times,voltage)))
    (tone/'metadata.json').write_text(json.dumps({'waveform':{'summary':{'channel':1}},'files':{'npy':'ch1.npy'}}),encoding='utf-8')
    recipe = Path(__file__).resolve().with_name('example_spectral_quality.toml')
    (root/'batch.toml').write_text('schema="wavebench.analysis_batch.v1"\nrecipe='+json.dumps(str(recipe))+
        '\non_failure="continue"\nduplicates="allow"\nmax_output_bytes=10000000\n'+
        ''.join(f'[[entries]]\nid="tone_{i}"\ncapture="tone"\nchannel=1\n' for i in (1,2)),encoding='utf-8')
    print(root)


if __name__ == '__main__':
    main()
