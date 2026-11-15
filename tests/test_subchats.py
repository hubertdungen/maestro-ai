import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_queue as Q
import maestro_workflow as WF
import maestro_web as W


def node(nid, deps=(), read_only=True):
    return dict(id=nid, title=nid, instruction='Investigate '+nid, engine='deepseek',
                read_only=read_only, role='analysis' if read_only else 'implementation', depends_on=list(deps))


class SubchatTests(unittest.TestCase):
    def test_prune_inactive_jobs_keeps_running_and_recent_workers(self):
        old = time.time() - W.ACTIVE_JOB_TTL_S - 1
        jobs = {
            'old': {'status': 'done', 'finished_at': old, 'children': []},
            'recent': {'status': 'done', 'finished_at': time.time(), 'children': []},
            'running': {'status': 'running', 't0': old, 'children': [
                {'status': 'done', 'finished_at': old}, {'status': 'running'}
            ]},
        }
        with patch.object(W, 'ACTIVE_JOBS', jobs):
            result = W.prune_inactive_jobs()
        self.assertEqual(result['removed_jobs'], ['old'])
        self.assertEqual(result['removed_children'], 1)
        self.assertIn('recent', jobs)
        self.assertIn('running', jobs)
        self.assertEqual(len(jobs['running']['children']), 1)

    def test_get_route_parses_subchat_query_and_returns_errors(self):
        handler = object.__new__(W.Handler)
        handler.path = '/api/chat/subchat?id=chat&turn_id=' + 'a'*32 + '&node_id=scout'
        with patch.object(W.Handler, '_lan_ok', return_value=True), \
             patch.object(W.Handler, '_reply') as reply, \
             patch.object(W, 'workflow_subchat', return_value={'node': {'id': 'scout'}}) as view:
            handler.do_GET()
            view.assert_called_once_with('chat', 'a'*32, 'scout')
            reply.assert_called_once_with(200, {'node': {'id': 'scout'}})
            reply.reset_mock()
            view.side_effect = ValueError('Subtarefa não encontrada.')
            handler.do_GET()
            reply.assert_called_once_with(400, {'error': 'Subtarefa não encontrada.'})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.flow = WF.Workflow(self.root/'workflows'/('a'*32+'.json'),
                                dict(nodes=[node('scout'), node('write', ['scout'], False)]), 'Main mission')

    def test_running_guidance_finishes_before_dependent_launch_and_survives_reload(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def execute(n, context):
            calls.append((n['id'], context))
            if len(calls) == 1:
                started.set()
                self.assertTrue(release.wait(3))
            return dict(text='answer '+str(len(calls)), agent_used='DeepSeek')
        thread = threading.Thread(target=lambda:self.flow.run(execute))
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            self.flow.guide('scout', 'Check another source', 'retry-safe')
            self.flow.guide('scout', 'Check another source', 'retry-safe')
            saved = json.loads(self.flow.path.read_text())
            self.assertEqual(len(saved['nodes'][0]['guidance']), 1)
            self.assertEqual(saved['nodes'][0]['guidance'][0]['status'], 'queued')
        finally:
            release.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual([c[0] for c in calls], ['scout', 'scout', 'write'])
        self.assertEqual(calls[1][1]['previous_result']['text'], 'answer 1')
        self.assertEqual(calls[2][1]['dependency_results']['scout']['text'], 'answer 2')
        loaded = WF.Workflow.load(self.flow.path).snapshot()
        self.assertEqual(loaded['nodes'][0]['guidance'][0]['status'], 'delivered')
        self.assertEqual(len(loaded['nodes'][0]['exchanges']), 2)
        self.assertTrue(loaded['nodes'][0]['read_only'])
        with self.assertRaises(ValueError):
            self.flow.guide('scout', 'too late')

    def test_pending_guidance_applied_on_first_launch(self):
        started, release = threading.Event(), threading.Event()
        contexts = {}
        def execute(n, context):
            contexts[n['id']] = context
            if n['id'] == 'scout':
                started.set(); release.wait(3)
            return dict(text='evidence')
        thread = threading.Thread(target=lambda:self.flow.run(execute));thread.start()
        try:
            self.assertTrue(started.wait(2))
            self.flow.guide('write', 'Keep the existing API')
        finally:
            release.set();thread.join(3)
        self.assertEqual(contexts['write']['guidance'][0]['text'], 'Keep the existing API')
        self.assertEqual(self.flow.snapshot()['status'], 'done')

    def test_view_checks_ownership_and_preserves_completed_state(self):
        self.flow.run(lambda n,c:dict(text='saved result'))
        queue = Q.TurnQueue(self.root/'queue.json')
        task, _ = queue.add(dict(id='chat', text='Main mission'))
        queue.tasks[0]['id'] = 'a'*32
        with patch.object(W, 'TURN_QUEUE', queue), patch.object(W.M, 'STATE_DIR', self.root), \
             patch.object(W, 'load_chat', return_value=dict(id='chat')), patch.object(W,'ACTIVE_JOBS',{}):
            data = W.workflow_subchat('chat', 'a'*32, 'scout')
            self.assertEqual(data['node']['result']['text'], 'saved result')
            self.assertFalse(data['can_guide'])
            for args in [('wrong-chat', 'a'*32, 'scout'), ('chat','../../secret','scout'), ('chat','a'*32,'unknown')]:
                with self.assertRaises(ValueError):W.workflow_subchat(*args)


class ParallelAdmissionTests(unittest.TestCase):
    def test_standalone_analysis_is_automatic_but_contextual_requests_wait(self):
        self.assertTrue(Q.independent_analysis(dict(text='Explica como funciona o arquivo de conversas')))
        for text in ['Tenho de atualizar a app agora?', 'Explica melhor o que disseste',
                     'Analisa isso depois', 'Explica como funciona e reinicia o servidor']:
            self.assertFalse(Q.independent_analysis(dict(text=text)), text)

    def test_workflow_uses_only_actual_workers_for_capacity_and_engine_choice(self):
        running = [dict(chat_id='main', workflow={'nodes':[]}, children=[dict(status='running', plan={'exec_engine':'codex'})])]
        plan = dict(available_engines=['deepseek'], exec_engine='deepseek', dim_level=1,
                    quotas={'deepseek':{'available':True}})
        task = dict(chat_id='main',text='Explica como funciona o arquivo de conversas',engine='auto')
        self.assertEqual(Q.decide(task, plan, running), ('deepseek','parallel'))
        running[0]['children'].extend(dict(status='running',plan={'exec_engine':'deepseek'}) for _ in range(Q.MAX_PARALLEL_AGENTS - 1))
        self.assertEqual(Q.decide(task,plan,running),(None,'capacity'))

    def test_global_capacity_blocks_fifth_agent_and_releases_slot(self):
        from contextlib import ExitStack
        slots = Q.ExecutionSlots()
        entered, waiting = threading.Event(), threading.Event()
        def extra():
            with slots.hold(True, waiting=waiting.set):
                entered.set()
        with ExitStack() as stack:
            for _ in range(Q.MAX_PARALLEL_AGENTS):
                stack.enter_context(slots.hold(True))
            thread = threading.Thread(target=extra)
            thread.start()
            self.assertTrue(waiting.wait(2))
            self.assertFalse(entered.is_set())
        thread.join(2)
        self.assertTrue(entered.is_set())
        self.assertEqual(slots.active, {})

    def test_shared_slots_prevent_second_writer_and_release_after_failure(self):
        slots=Q.ExecutionSlots();entered=threading.Event();waiting=threading.Event()
        def second():
            with slots.hold(False,waiting=waiting.set):entered.set()
        with slots.hold(False):
            thread=threading.Thread(target=second);thread.start()
            self.assertTrue(waiting.wait(2))
            self.assertFalse(entered.is_set())
            self.assertEqual(len(slots.active),1)
        thread.join(2);self.assertTrue(entered.is_set())
        with self.assertRaises(RuntimeError):
            with slots.hold(True):raise RuntimeError('provider failed')
        self.assertEqual(slots.active,{})


if __name__ == '__main__':unittest.main()
