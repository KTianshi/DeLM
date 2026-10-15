"""Sequential batch runner for detached execution; never retry or overwrite trials."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from experiments.math_harness.run import write_json
from experiments.math_harness.profiles import ROOT
from experiments.math_harness.export_results import export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--calibration-approved', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((args.bundle / 'manifest.json').read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    state = {'status': 'running', 'completed_run_ids': [], 'current_run_id': None}
    try:
        for plan in manifest['runs']:
            state['current_run_id'] = plan['run_id']
            write_json(args.output / 'batch.json', state)
            command = [sys.executable, '-m', 'experiments.math_harness.run', '--bundle', str(args.bundle.resolve()),
                       '--run-id', plan['run_id'], '--output', str(args.output.resolve() / plan['run_id'])]
            if args.calibration_approved:
                command.append('--calibration-approved')
            with (args.output / (plan['run_id'] + '.log')).open('x') as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, cwd=ROOT)
            result = json.loads((args.output / plan['run_id'] / 'run.json').read_text())
            # Timeouts remain valid observations; infrastructure and collection failures stop the batch.
            if any(w.get('usage_collection_error') or w['status'] == 'error' for w in result['workers']):
                raise RuntimeError('Trial has a worker/collection error; inspect it before continuing')
            state['completed_run_ids'].append(plan['run_id'])
            export(manifest, args.output)
        export(manifest, args.output, complete=True)
        state.update(status='completed_ungraded', current_run_id=None)
    except Exception as error:
        state.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        write_json(args.output / 'batch.json', state)


if __name__ == '__main__':
    main()
