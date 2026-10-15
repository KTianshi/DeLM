"""Preserve and validate native Codex session trees without extracting archive paths."""
import hashlib
import io
import json
import subprocess
import tarfile
from datetime import datetime


def seconds(value):
    return value if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()

from runtime.config import CLI_VERSIONS

KEYS = ('input_tokens', 'cached_input_tokens', 'output_tokens')


def parent_id(meta):
    source = meta.get('source')
    spawn = source.get('subagent', {}).get('thread_spawn', {}) if isinstance(source, dict) else {}
    return meta.get('parent_thread_id') or spawn.get('parent_thread_id')


def analyze(blobs, model, allow_children):
    sessions = {}
    for raw in blobs:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        metas = [r['payload'] for r in rows if r.get('type') == 'session_meta']
        if not metas or rows[0].get('type') != 'session_meta':
            raise ValueError('Expected native session metadata at the start')
        meta = metas[0]
        sid = meta.get('id')
        if not sid or meta.get('cli_version') != CLI_VERSIONS['codex'] or meta.get('model_provider') != 'openai':
            raise ValueError('Native identity/version/provider mismatch')
        if sid in sessions:
            if sessions[sid]['rows'] == rows:
                continue
            raise ValueError('Conflicting native copies of a session')
        turns = [r['payload'] for r in rows if r.get('type') == 'turn_context']
        if any((t.get('model'), t.get('effort')) != (model, 'xhigh') for t in turns):
            raise ValueError('Native model/effort mismatch')
        sessions[sid] = {'parent': parent_id(meta), 'rows': rows, 'agent_path': meta.get('agent_path'), 'inherited_metas': metas[1:]}
    roots = [sid for sid, s in sessions.items() if not s['parent']]
    if len(roots) != 1 or (not allow_children and len(sessions) != 1):
        raise ValueError('Unexpected root or subagent sessions')
    root = roots[0]
    for sid in sessions:
        seen = set()
        current = sid
        while current != root:
            if current in seen or current not in sessions:
                raise ValueError('Missing ancestor or cyclic session tree')
            seen.add(current)
            current = sessions[current]['parent']
        ancestor_ids = (seen | {root}) - {sid}
        if any(m.get('id') not in ancestor_ids for m in sessions[sid]['inherited_metas']):
            raise ValueError('Unexpected inherited session metadata')
    requested_paths, requested_ids = set(), set()
    for session in sessions.values():
        calls = set()
        for row in session['rows']:
            p = row.get('payload', {})
            if row.get('type') != 'response_item':
                continue
            if p.get('type') == 'function_call' and p.get('name', '').split('.')[-1] == 'spawn_agent':
                calls.add(p.get('call_id'))
            if p.get('type') == 'function_call_output' and p.get('call_id') in calls:
                try:
                    response = json.loads(p['output'])
                except (ValueError, TypeError):
                    raise ValueError('Unrecognized native spawn response')
                if response.get('task_name'):
                    requested_paths.add(response['task_name'])
                elif response.get('agent_id'):
                    requested_ids.add(response['agent_id'])
                elif not response.get('error'):
                    raise ValueError('Unrecognized successful native spawn response')
    if not requested_paths.issubset({s['agent_path'] for s in sessions.values()}) or not requested_ids.issubset(sessions):
        raise ValueError('Missing native trace for a spawned subagent')
    # Only native events emitted in this session count. Forked historical rows are
    # excluded by matching their turn IDs against ancestor-owned turns below.
    owned = {}
    reports = []
    usage = dict.fromkeys(KEYS, 0)
    ordered = sorted(sessions, key=lambda sid: depth(sid, sessions))
    for sid in ordered:
        rows = sessions[sid]['rows']
        ancestors = set()
        current = sessions[sid]['parent']
        while current:
            ancestors |= owned[current]
            current = sessions[current]['parent']
        turn = None
        turns = set()
        pending = set()
        last_total = None
        previous_marker = None
        session_usage = dict.fromkeys(KEYS, 0)
        starts, ends, durations = [], [], []
        for row in rows:
            p = row.get('payload', {})
            if row.get('type') == 'turn_context':
                turn = p.get('turn_id')
                if turn and turn not in ancestors:
                    turns.add(turn)
            if row.get('type') != 'event_msg':
                continue
            typ = p.get('type')
            if typ == 'task_started':
                turn = p.get('turn_id')
                if turn and turn not in ancestors:
                    turns.add(turn); pending.add(turn)
                    starts.append(p.get('started_at') or row.get('timestamp'))
            if typ == 'token_count' and p.get('info'):
                total = p['info'].get('total_token_usage')
                # Cumulative counters can repeat on quota-only notifications.
                marker = (turn, total)
                if turn not in ancestors and total is not None and marker != previous_marker:
                    last = p['info'].get('last_token_usage')
                    if last is None or any(type(last.get(k)) is not int or last[k] < 0 for k in KEYS):
                        raise ValueError('Missing valid per-response native usage')
                    for k in KEYS:
                        session_usage[k] += last[k]
                    last_total = total
                previous_marker = marker
            if typ == 'task_complete' and p.get('turn_id') not in ancestors:
                pending.discard(p.get('turn_id'))
                ends.append(p.get('completed_at') or row.get('timestamp'))
                durations.append(p.get('duration_ms'))
        owned[sid] = turns
        for k in KEYS:
            usage[k] += session_usage[k]
        latency = None
        if starts and ends:
            latency = durations[0] / 1000 if len(durations) == 1 and durations[0] is not None else seconds(ends[-1]) - seconds(starts[0])
        reports.append({'session_id': sid, 'parent_session_id': sessions[sid]['parent'],
                        'observed_usage': session_usage, 'usage_complete': bool(turns) and not pending and last_total is not None,
                        'latency_seconds': latency, 'turns': len(turns), 'unfinished_turns': len(pending)})
    return {'root_session_id': root, 'session_count': len(sessions), 'subagent_count': len(sessions)-1,
            'sessions': reports, 'usage': usage, 'usage_complete': all(r['usage_complete'] for r in reports),
            'root_latency_seconds': next(r['latency_seconds'] for r in reports if r['session_id'] == root)}


def depth(sid, sessions):
    count = 0
    while sessions[sid]['parent']:
        count += 1
        sid = sessions[sid]['parent']
    return count


def collect_tree(container, folder, model, allow_children):
    blobs = []
    for directory in ('sessions', 'archived_sessions'):
        copied = subprocess.run(['docker', 'cp', f'{container}:/root/.codex/{directory}', '-'],
                                capture_output=True, timeout=60)
        if copied.returncode:
            if directory == 'archived_sessions' and b'Could not find' in copied.stderr:
                continue
            raise RuntimeError(f'Cannot collect native {directory}: {copied.stderr.decode(errors="replace")}')
        (folder / f'native-{directory}.tar').write_bytes(copied.stdout)
        with tarfile.open(fileobj=io.BytesIO(copied.stdout)) as archive:
            for member in archive.getmembers():
                if member.isfile() and member.name.endswith('.jsonl'):
                    raw = archive.extractfile(member).read()
                    blobs.append(raw)
                    (folder / ('native-' + hashlib.sha256(raw).hexdigest()[:20] + '.jsonl')).write_bytes(raw)
    tree = analyze(blobs, model, allow_children)
    (folder / 'native-tree.json').write_text(json.dumps(tree, indent=2)+'\n')
    return tree
