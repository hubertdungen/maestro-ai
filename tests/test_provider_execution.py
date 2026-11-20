"""Exercise real provider adapters with fake processes; no inference or disk writes."""
import io
import json
import time
import pathlib
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_models as MODELS
import maestro_web as W


class FakeProcess:
    def __init__(self, output='Completed', rc=0, err=''):
        self.output, self.returncode, self.error = output, rc, err
        self.stdout, self.stderr = io.StringIO(output), io.StringIO(err)
        self.pid = 99999999
    def communicate(self, timeout=None):
        return self.output, self.error
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        return self.returncode
    def kill(self):
        self.returncode = -9


class ProviderExecutionTests(unittest.TestCase):
    def setUp(self):
        self.entries = [MODELS._entry('codex', 'gpt-test-terra', 'Terra', ['low', 'high'], 'low', 'test'),
                        MODELS._entry('sonnet', 'sonnet', 'Sonnet', ['low', 'high'], None, 'test'),
                        MODELS._entry('fable', 'fable', 'Fable', ['low', 'high'], None, 'test'),
                        MODELS._entry('agy', 'gemini-test-high', 'Gemini', [], None, 'test')]
        self.patches = [patch.object(W.EFFICIENCY, 'record_call'), patch.object(W.MODELS, 'catalog', return_value={'models': self.entries}),
                        patch.object(W, 'handover_prompt_block', return_value=''),
                        patch.object(W.M, 'record_agent_history'),
                        patch.object(W.M, 'update_shared_context'),
                        patch.object(W.F, 'save_artifact'),
                        patch.object(W.M, 'get_claude_quota', return_value={'pace_status': 'ok', 'seven_day_pct': 20}),
                        patch.object(W.M, '_extract_deepseek_key', return_value=None),
                        patch.object(W, 'AGENT_TIMEOUT_S', 0)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
        self.commands = []
    def invoke(self, engine, model_id=None, effort=None, readonly=False, output='Completed', rc=0, job=None, **extra):
        plan = dict(quotas={'codex': {'seven_day_pct': 20, 'seven_day_reset': time.time()+3*86400}}, domain='NAS', dimension='Grande', agents=['test'], reason='adapter test',
                    exec_engine=engine, _workflow_internal=True, read_only=readonly, **extra)
        if model_id:
            plan.update(model_id=model_id, effort=effort)
        def spawn(cmd, **kwargs):
            self.commands.append(cmd)
            return FakeProcess(output, rc)
        with patch.object(W.subprocess, 'Popen', side_effect=spawn):
            return W.execute_agent_turn(engine, 'Test task', 'Test task', '/tmp', [], plan, job=job)

    def test_codex_activity_keeps_command_and_extracts_final_answer(self):
        output = '\n'.join(json.dumps(e) for e in [
            {'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'rg needle app.py | head'}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Done'}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'output_tokens': 20}}])
        job = {'status': 'running'}
        r = self.invoke('codex', output=output, job=job)
        self.assertEqual(r['text'], 'Done')
        self.assertEqual(r['usage']['total_tokens'], 120)
        self.assertIn('rg needle app.py | head', job['activity_detail'])
        self.assertGreater(job['activity_ts'], 0)
        self.assertNotIn('item.started', job.get('last_line', ''))
        self.assertIn('--json', self.commands[0])

    def test_codex_mcp_activity_exposes_metadata_without_result_or_reasoning(self):
        from maestro_activity import ActivityStream
        events = ActivityStream('codex')
        for phase in ('started', 'completed'):
            hint = events.feed(json.dumps({'type': 'item.' + phase, 'item': {
                'type': 'mcp_tool_call', 'server': 'functions', 'tool': 'exec_command',
                'arguments': '{"cmd":"cat app.py","content":"private input"}',
                'result': {'text': 'private result'}}}))
            self.assertIn('cat app.py', hint)
            self.assertNotIn('private', hint)
            self.assertIn('terminou' if phase == 'completed' else 'A executar', hint)
        self.assertIsNone(events.feed(json.dumps({'type': 'item.completed', 'item': {
            'type': 'reasoning', 'text': 'private reasoning'}})))

    def test_live_progress_keeps_public_updates_and_actions_separate_from_answer(self):
        output = '\n'.join(json.dumps(e) for e in [
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Vou verificar os ficheiros.'}},
            {'type': 'item.completed', 'item': {'type': 'reasoning', 'text': 'private reasoning'}},
            {'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'rg needle app.py'}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Validado.'}}])
        job = {'status': 'running'}
        result = self.invoke('codex', output=output, job=job)
        self.assertEqual(result['text'], 'Validado.')
        self.assertEqual([e['kind'] for e in job['progress_events']], ['update', 'action', 'update'])
        self.assertEqual(job['progress_events'][0]['text'], 'Vou verificar os ficheiros.')
        self.assertNotIn('private reasoning', json.dumps(job['progress_events']))

    def test_claude_live_public_text_excludes_thinking(self):
        output = '\n'.join(json.dumps(e) for e in [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'thinking', 'thinking': 'private reasoning'},
                {'type': 'text', 'text': 'A validar o resultado.'}]}},
            {'type': 'result', 'result': 'Done'}])
        job = {'status': 'running'}
        self.assertEqual(self.invoke('sonnet', output=output, job=job)['text'], 'Done')
        self.assertEqual([e['text'] for e in job['progress_events']], ['A validar o resultado.'])

    def test_live_progress_history_is_bounded(self):
        output = '\n'.join(json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': 'Update ' + str(n)}}) for n in range(65))
        job = {'status': 'running'}
        self.invoke('codex', output=output, job=job)
        self.assertEqual(len(job['progress_events']), 40)
        self.assertEqual(job['progress_events'][0]['text'], 'Update 25')

    def test_claude_activity_shows_path_without_file_contents(self):
        output = '\n'.join(json.dumps(e) for e in [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'thinking', 'thinking': 'Private reasoning'},
                {'type': 'tool_use', 'name': 'Write', 'input': {'file_path': '/tmp/app.py', 'content': 'Private file contents'}}]}},
            {'type': 'result', 'result': 'Saved'}])
        job = {'status': 'running'}
        r = self.invoke('sonnet', output=output, job=job)
        self.assertEqual(r['text'], 'Saved')
        self.assertEqual(job['activity_detail'], 'A escrever: /tmp/app.py')
        self.assertIn('--verbose', self.commands[0])
        # The synchronous path also returns the answer, never the JSON envelope.
        self.assertEqual(self.invoke('sonnet', output=output)['text'], 'Saved')

    def test_stream_failure_preserves_provider_error(self):
        output = json.dumps({'type': 'turn.failed', 'error': {'message': 'Provider unavailable'}})
        r = self.invoke('codex', output=output, rc=17, job={'status': 'running'})
        self.assertEqual(r['rc'], 17)
        self.assertIn('Provider unavailable', r['text'])

    def test_codex_uses_selected_model_effort_and_readonly(self):
        r = self.invoke('codex', 'codex:gpt-test-terra', 'high', readonly=True)
        cmd = self.commands[0]
        self.assertEqual(cmd[cmd.index('-m')+1], 'gpt-test-terra')
        self.assertEqual(cmd[cmd.index('-s')+1], 'read-only')
        self.assertIn('model_reasoning_effort="high"', cmd)
        self.assertIn('approval_policy="never"', cmd)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', cmd)
        self.assertIn('gpt-test-terra', r['agent_used'])
        self.assertEqual(r['model_id'], 'codex:gpt-test-terra')
        self.assertEqual(r['rc'], 0)

    def test_claude_readonly_restricts_tools_and_removes_bypass(self):
        r = self.invoke('sonnet', 'sonnet:sonnet', 'high', readonly=True)
        cmd = self.commands[0]
        self.assertEqual(cmd[cmd.index('--model')+1], 'sonnet')
        self.assertEqual(cmd[cmd.index('--effort')+1], 'high')
        self.assertNotIn('--dangerously-skip-permissions', cmd)
        self.assertEqual(cmd[cmd.index('--permission-mode')+1], 'dontAsk')
        self.assertEqual(cmd[cmd.index('--tools')+1], 'Read,Glob,Grep,WebSearch,WebFetch,Bash')
        allowed = set(cmd[cmd.index('--allowedTools')+1].split(','))
        self.assertLessEqual({'Read', 'Glob', 'Grep', 'WebSearch', 'WebFetch'}, allowed)
        # Fixed inspection entrypoints accept repo paths without cd/timeout.
        self.assertNotIn('Bash', allowed)
        shell = sorted(a for a in allowed if a.startswith('Bash('))
        self.assertEqual(len(shell), 2)
        self.assertTrue(shell[0].endswith('/tools/inspect_repo.py:*)'))
        self.assertTrue(shell[1].endswith('/tools/ver.py:*)'))
        prompt = cmd[cmd.index('-p')+1]
        self.assertIn('PERMISSÕES REAIS DESTA SUBTAREFA', prompt)
        self.assertIn('/tools/inspect_repo.py status /tmp', prompt)
        self.assertIn('sem cd, timeout, pipes', prompt)
        self.assertIn(str(pathlib.Path(__file__).resolve().parents[1]), cmd)
        self.assertIn('--strict-mcp-config', cmd)
        self.assertEqual(r['plan']['exec_engine'], 'sonnet')

    def test_claude_fable_keeps_explicit_alias(self):
        r = self.invoke('fable', 'fable:fable', 'low')
        cmd = self.commands[0]
        self.assertEqual(cmd[cmd.index('--model')+1], 'fable')
        self.assertIn('Fable', r['agent_used'])
        self.assertEqual(r['plan']['exec_engine'], 'fable')
        self.assertIn('--dangerously-skip-permissions', cmd)
        self.assertNotIn('--permission-mode', cmd)
        self.assertNotIn('PERMISSÕES REAIS DESTA SUBTAREFA', cmd[cmd.index('-p')+1])

    def test_agy_uses_exact_selected_id(self):
        out = '{"event":"result","result":{"response":"Completed"}}\n'
        r = self.invoke('agy', 'agy:gemini-test-high', output=out)
        cmd = self.commands[0]
        self.assertEqual(cmd[cmd.index('--model')+1], 'gemini-test-high')
        self.assertIn('gemini-test-high', r['agent_used'])
        self.assertEqual(r['rc'], 0)

    def test_process_failure_is_preserved_even_when_stdout_nonempty(self):
        r = self.invoke('codex', 'codex:gpt-test-terra', rc=17, output='Partial answer')
        self.assertEqual(r['rc'], 17)
        self.assertEqual(r['text'], 'Partial answer')

    def test_readonly_agy_never_starts_unconfined_process(self):
        try:
            r = self.invoke('agy', 'agy:gemini-test-high', readonly=True,
                            output='{"event":"result","result":{"response":"Completed"}}\n')
        except ValueError:
            pass
        else:
            self.assertNotEqual(r['rc'], 0)
        self.assertEqual(self.commands, [])

    def test_agy_planning_runs_in_plan_mode_without_skipping_permissions(self):
        self.invoke('agy', 'agy:gemini-test-high', readonly=True, no_tools=True,
                    output='{"event":"result","result":{"response":"{}"}}\n')
        cmd = self.commands[-1]
        self.assertIn('--mode', cmd)
        self.assertEqual(cmd[cmd.index('--mode') + 1], 'plan')
        self.assertNotIn('--dangerously-skip-permissions', cmd)

    def test_provider_model_mismatch_rejected_before_spawn(self):
        try:
            r = self.invoke('sonnet', 'codex:gpt-test-terra', 'high')
        except ValueError:
            pass
        else:
            self.assertNotEqual(r['rc'], 0)
        self.assertEqual(self.commands, [])

if __name__ == '__main__':
    unittest.main()
