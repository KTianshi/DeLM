"""Export collected observations and anonymized proofs, without grading them."""
import json
import secrets
import shutil
from experiments.math_harness.summarize import summarize
from experiments.math_harness.profiles import profile_dir


def export(manifest, output, complete=False):
    records = []
    for plan in manifest['runs']:
        path = output / plan['run_id'] / 'normalized.json'
        if path.exists():
            records.append(json.loads(path.read_text()))
    (output / 'records.json').write_text(json.dumps(records, indent=2)+'\n')
    (output / 'summary.json').write_text(json.dumps(summarize(manifest, records), indent=2)+'\n')
    if not complete:
        return
    packet = output / 'review-packet'
    packet.mkdir(exist_ok=False)
    mapping = {}
    submissions = [(r, w) for r in records for w in r['workers'] if w['status']=='submitted']
    secrets.SystemRandom().shuffle(submissions)
    for record, worker in submissions:
        label = 'proof-' + secrets.token_hex(8)
        shutil.copyfile(output / record['run_id'] / worker['worker_id'] / 'final.txt', packet / (label+'.txt'))
        mapping[label] = {'run_id': record['run_id'], 'worker_id': worker['worker_id']}
    source = profile_dir(manifest["configuration"])
    shutil.copyfile(source / 'problems' / (manifest['configuration']['problem_id']+'.txt'), packet / 'PROBLEM.txt')
    rubric = source / 'grader' / (manifest['configuration']['problem_id']+'.json')
    if rubric.exists():
        shutil.copyfile(rubric, packet / 'rubric.json')
    (packet / 'reviews.json').write_text(json.dumps({key: {'verdict':'ungraded','reviewer':None,'notes':None} for key in mapping},indent=2)+'\n')
    (packet / 'README.txt').write_text('Review each complete proof independently. Accept valid alternative arguments. Mark uncertain gaps explicitly. No proof is automatically graded. Filenames omit condition and timing; verbatim proof text can still reveal collaboration. Mapping is kept outside this packet.\n')
    (output / 'review-mapping-private.json').write_text(json.dumps(mapping,indent=2)+'\n')
