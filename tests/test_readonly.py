import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_readonly as R


def answer(content='Verified', calls=None, finish='stop'):
    return {'choices': [{'message': {'role': 'assistant', 'content': content, 'tool_calls': calls}, 'finish_reason': finish}]}


def call(name, args):
    return {'id': 'call-1', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


class ReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.project = self.root/'project'; self.project.mkdir()
        (self.project/'app.py').write_text('first\nneedle here\nthird\n')
        (self.root/'outside.txt').write_text('outside secret')
        (self.project/'escape').symlink_to(self.root, target_is_directory=True)
        (self.project/'.env').write_text('SECRET_TOKEN=private')
        (self.project/'fifo').touch()  # Replaced by real FIFO in the special-file test.
        self.scope = R.Workspace(self.project); self.addCleanup(self.scope.close)
        p = patch.object(R, '_key', return_value='test-key-not-real'); p.start(); self.addCleanup(p.stop)
        p = patch.object(R, 'discover_models', return_value=[{'id': 'deepseek-flash', 'effort': {'supported_levels': ['low']}}]); p.start(); self.addCleanup(p.stop)

    def test_read_and_search_have_evidence_line_numbers(self):
        self.assertIn('2: needle here', self.scope.read_file('app.py')['text'])
        self.assertEqual(self.scope.search_text('needle')['matches'][0]['line'], 2)

    def test_search_accepts_explicit_file_and_reaches_late_source(self):
        (self.project/'large.py').write_text(('filler line\n'*6000)+'late_ui_marker\n')
        match = self.scope.search_text('late_ui_marker', 'large.py')['matches']
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]['line'], 6001)
        self.assertEqual(match[0]['path'], 'large.py')
        self.assertEqual(len(self.scope.search_text('late_ui_marker')['matches']), 1)

    def test_recursive_search_skips_backup_and_binary_files(self):
        (self.project/'app.py.bak-today').write_text('backup_marker')
        (self.project/'picture.png').write_text('backup_marker')
        self.assertEqual(self.scope.search_text('backup_marker')['matches'], [])
        # Explicit file requests still work: backup inspection may be intentional.
        self.assertEqual(len(self.scope.search_text('backup_marker', 'app.py.bak-today')['matches']), 1)

    def test_symlink_traversal_outside_and_credentials_rejected(self):
        for path in ['../outside.txt', str(self.root/'outside.txt'), 'escape/outside.txt', '.env']:
            with self.assertRaises((OSError, ValueError)):
                self.scope.read_file(path)
        names = [e['name'] for e in self.scope.list_files()['entries']]
        self.assertNotIn('escape', names)
        self.assertNotIn('.env', names)

    def test_only_named_read_tools_exist(self):
        for name in ['Bash', 'exec', '__class__', 'write_file', 'fetch_url']:
            with self.assertRaises(ValueError):
                self.scope.call(name, {'command': 'touch anything'})
        with self.assertRaises(ValueError):
            self.scope.call('read_file', {'path': 'app.py', 'mode': 'w'})

    def test_special_files_rejected_without_blocking(self):
        import os
        fifo = self.project/'fifo'; fifo.unlink(); os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            self.scope.read_file('fifo')

    def test_extra_roots_and_exact_files_do_not_grant_siblings(self):
        extra = self.root/'extra'; extra.mkdir(); (extra/'safe.txt').write_text('safe')
        scope = R.ReadScope(self.project, allowed_roots=[extra], allowed_files=[self.root/'outside.txt'])
        self.addCleanup(scope.close)
        self.assertIn('safe', scope.call('read_file', {'path': str(extra/'safe.txt')})['text'])
        self.assertIn('outside secret', scope.call('read_file', {'path': str(self.root/'outside.txt')})['text'])
        with self.assertRaises(ValueError):
            scope.call('list_files', {'path': str(self.root)})

    def test_tool_loop_uses_protocol_and_cannot_write(self):
        sent = []
        replies = [answer(None, [call('read_file', {'path': 'app.py'})], 'tool_calls'), answer('Found needle in app.py:2')]
        def request(method, path, key, payload=None):
            sent.append(json.loads(json.dumps(payload)))
            return replies.pop(0)
        with patch.object(R, '_request', side_effect=request):
            result = R.run('Inspect app', self.project, model='deepseek-flash')
        self.assertEqual(result['rc'], 0)
        self.assertEqual(result['model_id'], 'deepseek-api:deepseek-flash')
        self.assertEqual(sent[1]['messages'][-1]['role'], 'tool')
        self.assertIn('needle here', sent[1]['messages'][-1]['content'])
        self.assertEqual({t['function']['name'] for t in sent[0]['tools']}, {'list_files', 'read_file', 'search_text'})
        self.assertEqual(sent[0]['thinking'], {'type': 'disabled'})
        self.assertEqual((self.project/'app.py').read_text(), 'first\nneedle here\nthird\n')

    def test_unauthorized_tool_is_reported_as_error_without_execution(self):
        replies = [answer(None, [call('Bash', {'command': 'touch pwned'})], 'tool_calls'), answer('Cannot run shell')]
        with patch.object(R, '_request', side_effect=lambda *a: replies.pop(0)):
            result = R.run('Inspect', self.project, model='deepseek-flash')
        self.assertNotEqual(result['rc'], 0)
        self.assertEqual(result['tools_failed'], ['Bash'])
        self.assertFalse((self.project/'pwned').exists())
        self.assertEqual(result['tools'], [])

    def test_stopped_before_inference_and_invalid_model(self):
        with patch.object(R, '_request') as request:
            result = R.run('Inspect', self.project, model='deepseek-flash', stopped=lambda: True)
            self.assertEqual(result['rc'], 130)
            request.assert_not_called()
            result = R.run('Inspect', self.project, model='imagined-model')
            self.assertNotEqual(result['rc'], 0)
            request.assert_not_called()

    def test_call_budget_and_provider_errors_are_bounded(self):
        too_many = answer(None, [call('read_file', {'path': 'app.py'})]*9, 'tool_calls')
        with patch.object(R, '_request', return_value=too_many) as request:
            result = R.run('Inspect', self.project, model='deepseek-flash')
            self.assertNotEqual(result['rc'], 0)
            self.assertEqual(request.call_count, 1)
        with patch.object(R, '_request', side_effect=R.requests.ConnectionError('private-url-token-secret')):
            result = R.run('Inspect', self.project, model='deepseek-flash')
            self.assertNotIn('private-url-token-secret', result['text'])

    def test_truncated_api_response_is_not_reported_success(self):
        with patch.object(R, '_request', return_value=answer('Partial', finish='length')):
            result = R.run('Inspect', self.project, model='deepseek-flash')
        self.assertEqual(result['rc'], 1)

if __name__ == '__main__':
    unittest.main()
