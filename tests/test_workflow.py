import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_workflow as W


def node(nid, deps=(), read_only=True, engine='codex'):
    return dict(id=nid, title=nid, instruction='Do '+nid, engine=engine,
                role='analysis' if read_only else 'implementation', read_only=read_only, depends_on=list(deps))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / 'workflow.json'

    def tearDown(self):
        self.tmp.cleanup()

    def workflow(self, nodes):
        plan = W.validate_plan({'nodes': nodes}, ['codex', 'deepseek'])
        return W.Workflow(self.path, plan, 'The objective')

    def test_mixed_question_and_action_trigger_but_single_request_does_not(self):
        for text in ('Já considera as quotas de 5h? Corrige também o hover.',
                     'Does it check quotas? Also fix the menu.',
                     'Corrige o menu. Já agora, qual é a quota?'):
            self.assertTrue(W.needs_workflow(text), text)
        for text in ('Podes implementar o botão?', 'Can you fix the menu?',
                     'Qual é a quota de 5h?', 'Corrige o hover do menu.'):
            self.assertFalse(W.needs_workflow(text), text)

    def test_parallel_readers_pass_results_only_after_both_finish(self):
        flow = self.workflow([node('a'), node('b'), node('write', ['a', 'b'], False), node('review', ['write'])])
        barrier = threading.Barrier(2)
        events = []
        lock = threading.Lock()

        def execute(n, context):
            if n['id'] in ('a', 'b'):
                barrier.wait(timeout=2)
            with lock:
                events.append(n['id'])
            if n['id'] == 'write':
                self.assertEqual(set(context['dependency_results']), {'a', 'b'})
                self.assertEqual(context['dependency_results']['a']['text'], 'result a')
            return {'text': 'result '+n['id'], 'agent_used': n['engine']}
        result = flow.run(execute)
        self.assertEqual(result['status'], 'done')
        self.assertEqual(set(events[:2]), {'a', 'b'})
        self.assertEqual(events[2:], ['write', 'review'])
        self.assertEqual(json.loads(self.path.read_text())['status'], 'done')

    def test_four_independent_agents_finish_before_dependent_delivery(self):
        names = ['a', 'b', 'c', 'd']
        flow = self.workflow([node(n) for n in names] + [node('delivery', names)])
        barrier = threading.Barrier(4)
        def execute(n, context):
            if n['id'] in names:
                barrier.wait(timeout=2)
            else:
                self.assertEqual(set(context['dependency_results']), set(names))
            return {'text': n['id']}
        result = flow.run(execute)
        self.assertEqual(result['status'], 'done')

    def test_writes_are_exclusive_even_without_dependencies(self):
        flow = self.workflow([node('w1', read_only=False), node('r'), node('w2', read_only=False)])
        active = []
        lock = threading.Lock()

        def execute(n, context):
            with lock:
                if not n['read_only']:
                    self.assertEqual(active, [])
                elif active:
                    self.assertTrue(all(x['read_only'] for x in active))
                active.append(n)
            time.sleep(.015)
            with lock:
                active.remove(n)
            return {'text': n['id']}
        self.assertEqual(flow.run(execute)['status'], 'done')

    def test_failure_blocks_descendants_but_independent_work_completes(self):
        flow = self.workflow([node('a'), node('b', ['a']), node('c', ['b']), node('independent')])
        calls = []

        def execute(n, context):
            calls.append(n['id'])
            if n['id'] == 'a':
                raise RuntimeError('broken')
            return {'text': 'ok'}
        result = flow.run(execute)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(set(calls), {'a', 'independent'})
        self.assertEqual([n['status'] for n in result['nodes']], ['failed', 'blocked', 'blocked', 'done'])

    def test_completed_nodes_never_replayed_on_load(self):
        flow = self.workflow([node('a'), node('b', ['a'])])
        flow.run(lambda n, c: {'text': n['id']})
        loaded = W.Workflow.load(self.path)
        self.assertEqual(loaded.run(lambda n, c: self.fail('must not replay'))['status'], 'done')

    def test_interrupted_write_requires_explicit_retry(self):
        flow = self.workflow([node('a', read_only=False), node('b', ['a'])])
        value = flow.snapshot()
        value['nodes'][0]['status'] = 'running'
        self.path.write_text(json.dumps(value))
        loaded = W.Workflow.load(self.path)
        result = loaded.run(lambda n, c: self.fail('must not replay'))
        self.assertEqual(result['nodes'][0]['status'], 'interrupted')
        loaded.retry('a')
        calls = []
        result = loaded.run(lambda n, c: calls.append(n['id']) or {'text': 'ok'})
        self.assertEqual(calls, ['a', 'b'])
        self.assertEqual(result['status'], 'done')

    def test_retry_only_repeats_failed_path(self):
        flow = self.workflow([node('a'), node('b', ['a']), node('c', ['b'])])
        flow.run(lambda n, c: {'text': 'fail', 'ok': False} if n['id'] == 'b' else {'text': 'ok'})
        flow.retry('b')
        calls = []
        result = flow.run(lambda n, c: calls.append(n['id']) or {'text': 'ok'})
        self.assertEqual(calls, ['b', 'c'])
        self.assertEqual(result['status'], 'done')

    def test_cancellation_does_not_launch_waiting_nodes(self):
        flow = self.workflow([node('a'), node('b', ['a'])])
        stop = threading.Event()
        gate = threading.Event()
        launched = threading.Event()

        def execute(n, context):
            launched.set()
            gate.wait(2)
            return {'text': 'ok'}
        timer = threading.Thread(target=lambda: (launched.wait(2), stop.set()), daemon=True)
        timer.start()
        try:
            result = flow.run(execute, stopped=stop.is_set)
            self.assertEqual(result['status'], 'cancelled')
            self.assertEqual([n['status'] for n in result['nodes']], ['interrupted', 'cancelled'])
        finally:
            gate.set()
            timer.join(2)

    def test_planner_called_once_and_invalid_output_falls_back(self):
        calls = []
        plan = W.build_plan('Check and improve', ['codex', 'deepseek'], 'codex',
                            lambda prompt: calls.append(prompt) or 'not json')
        self.assertEqual(len(calls), 1)
        self.assertEqual(plan['planning_source'], 'fallback')
        self.assertIn('planning_error', plan)
        self.assertEqual([n['role'] for n in plan['nodes']], ['scout', 'implementation', 'review'])

    def test_valid_planner_graph_preserved(self):
        plan = W.build_plan('Compare two things', ['codex'], 'codex',
                            lambda prompt: '```json\n'+json.dumps({'nodes': [node('a'), node('b')]})+'\n```')
        self.assertEqual(plan['planning_source'], 'coordinator')
        self.assertEqual(len(plan['nodes']), 2)

    def test_invalid_graphs_rejected(self):
        bad = [
            [node('a', ['missing'])],
            [node('a', ['b']), node('b', ['a'])],
            [node('a'), node('a')],
            [node('../a')],
            [node('a', engine='unavailable')],
            [dict(node('a', read_only=False), role='scout')],
            [dict(node('a'), read_only='false')],
            [node('a'+str(i)) for i in range(7)],
        ]
        for nodes in bad:
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                W.validate_plan({'nodes': nodes}, ['codex'])

    def test_turn_identity_cannot_silently_change(self):
        flow = self.workflow([node('a')])
        with self.assertRaises(ValueError):
            W.Workflow(self.path, {'nodes': [node('a')]}, 'different objective')
        with self.assertRaises(ValueError):
            W.Workflow(self.path, {'nodes': [node('b')]}, 'The objective')

    def test_simple_request_stays_single_turn(self):
        self.assertFalse(W.needs_workflow('Tira o ícone do placeholder', {'dim_level': 2}))
        self.assertTrue(W.needs_workflow('Vê o que dá para melhorar no maestro e melhora'))
        self.assertTrue(W.needs_workflow('Divide por subtarefas'))
        # Uma ordem que já traz a divisão por agentes não pode cair num turno de um só agente.
        self.assertTrue(W.needs_workflow('Entregar: 1. cenas.\n\nDivisão do trabalho (escritores em paralelo):\n- C++ com `write_paths` em Source/'))
        self.assertFalse(W.needs_workflow('Corre os testes em paralelo.'))


if __name__ == '__main__':
    unittest.main()
