"""Run multiple frozen variant bundles in alternating order, detached from SSH."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from experiments.math_harness.run import write_json
from experiments.math_harness.profiles import ROOT
from experiments.math_harness.export_results import export


def interleave(manifests):
    lengths={len(m['runs']) for m in manifests}
    if len(lengths)!=1:
        raise ValueError('Variants must have equal numbers of repetitions')
    for i in range(next(iter(lengths))):
        order=range(len(manifests)) if i%2==0 else reversed(range(len(manifests)))
        for j in order:
            yield j,manifests[j]['runs'][i]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundles',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--calibration-approved',action='store_true')
    args=parser.parse_args()
    bundles=[p.resolve() for p in args.bundles]
    if len({p.name for p in bundles})!=len(bundles):
        raise ValueError('Bundle names must be distinct')
    manifests=[json.loads((p/'manifest.json').read_text()) for p in bundles]
    planned=list(interleave(manifests))
    args.output.mkdir(parents=True,exist_ok=False)
    destinations=[args.output/p.name for p in bundles]
    for p in destinations:p.mkdir()
    state={'status':'running','planned_trials':len(planned),'completed':[],'current':None}
    try:
        for j,plan in planned:
            state['current']={'bundle':bundles[j].name,'run_id':plan['run_id'],'peers':plan['worker_count']}
            write_json(args.output/'batch.json',state)
            command=[sys.executable,'-m', 'experiments.math_harness.run','--bundle',str(bundles[j]),'--run-id',plan['run_id'],'--output',str((destinations[j]/plan['run_id']).resolve())]
            if args.calibration_approved:command.append('--calibration-approved')
            with (destinations[j]/(plan['run_id']+'.log')).open('x') as log:
                subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,cwd=ROOT)
            result=json.loads((destinations[j]/plan['run_id']/'run.json').read_text())
            if any(w.get('usage_collection_error') or w['status']=='error' for w in result['workers']):
                raise RuntimeError('Worker or collection error; inspect before continuing')
            state['completed'].append(state['current'])
            export(manifests[j],destinations[j])
        for j in range(len(bundles)):export(manifests[j],destinations[j],complete=True)
        state.update(status='completed_ungraded',current=None)
    except Exception as error:
        state.update(status='failed',error=f'{type(error).__name__}: {error}')
        raise
    finally:
        write_json(args.output/'batch.json',state)


if __name__=='__main__':main()
