import json
import unittest
from experiments.math_harness.native_records import analyze
from runtime.config import CLI_VERSIONS


def row(kind, **payload):
    return {'type': kind, 'payload': payload}


def events(sid, parent=None, inherited=(), complete=True, model='gpt-6-astra', total=10, path=None, last=None):
    meta = row('session_meta', id=sid, cli_version=CLI_VERSIONS['codex'], model_provider='openai', parent_thread_id=parent, agent_path=path)
    values = [meta, *inherited,
              row('event_msg', type='task_started', turn_id=sid+'-turn', started_at=100),
              row('turn_context', turn_id=sid+'-turn', model=model, effort='xhigh'),
              row('event_msg', type='token_count', info={'total_token_usage': {'input_tokens': total, 'cached_input_tokens': 0, 'output_tokens': total}, 'last_token_usage': {'input_tokens': total if last is None else last, 'cached_input_tokens': 0, 'output_tokens': total if last is None else last}})]
    if complete:
        values.append(row('event_msg', type='task_complete', turn_id=sid+'-turn', started_at=100, completed_at=102, duration_ms=2123))
    return values


def blobs(*records):
    return ['\n'.join(map(json.dumps, r)).encode() for r in records]


class NativeRecordsTests(unittest.TestCase):
    def test_native_tree_counts_children_and_native_duration(self):
        result=analyze(blobs(events('root'),events('child','root')), 'gpt-6-astra', True)
        self.assertEqual(result['usage']['input_tokens'],20)
        self.assertEqual(result['root_latency_seconds'],2.123)
        self.assertTrue(result['usage_complete'])

    def test_inherited_history_and_repeated_counter_are_not_counted_twice(self):
        root=events('root')
        child=events('child','root',inherited=root,total=15,last=5)
        child.insert(-1,child[-2])
        result=analyze(blobs(root,child),'gpt-6-astra',True)
        self.assertEqual(result['usage']['input_tokens'],15)

    def test_fork_counter_can_restart_after_inherited_history(self):
        root=events('root',total=100)
        child=events('child','root',inherited=root,total=12)
        result=analyze(blobs(root,child),'gpt-6-astra',True)
        self.assertEqual(result['usage']['input_tokens'],112)

    def test_unfinished_child_makes_whole_total_partial(self):
        result=analyze(blobs(events('root'),events('child','root',complete=False)),'gpt-6-astra',True)
        self.assertFalse(result['usage_complete'])

    def test_missing_spawned_child_is_rejected(self):
        root=events('root')+[row('response_item',type='function_call',name='spawn_agent',call_id='c'),row('response_item',type='function_call_output',call_id='c',output=json.dumps({'task_name':'/root/helper'}))]
        with self.assertRaisesRegex(ValueError,'Missing native trace'):
            analyze(blobs(root),'gpt-6-astra',True)

    def test_wrong_model_children_in_controls_and_missing_parent_rejected(self):
        for records, allow in (([events('root'),events('child','root',model='wrong')],True),([events('root'),events('child','root')],False),([events('root'),events('child','absent')],True)):
            with self.assertRaises(ValueError):
                analyze(blobs(*records),'gpt-6-astra',allow)

    def test_identical_archived_copy_deduplicated_conflicting_copy_rejected(self):
        result=analyze(blobs(events('root'),events('root')),'gpt-6-astra',False)
        self.assertEqual(result['session_count'],1)
        with self.assertRaises(ValueError):
            analyze(blobs(events('root'),events('root',total=20)),'gpt-6-astra',False)
