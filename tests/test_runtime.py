import copy
import json
import time
import pathlib
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_runtime as R
import maestro_queue as Q
import maestro_workflow as WF


def entry(engine, model, readonly=True, available=True):
    return dict(id=engine+':'+model, engine=engine, model=model, efforts=['low','high'],
                default_effort='low', read_only_enforced=readonly, available=available)


def node(nid, deps=(), engine='codex', read_only=True, **kwargs):
    return dict(id=nid, title=nid, instruction='Work '+nid, engine=engine,
                role='analysis' if read_only else 'implementation', read_only=read_only,
                depends_on=list(deps), **kwargs)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.entries = [entry('codex','gpt-sol'), entry('codex','gpt-luna'),
                        entry('sonnet','sonnet'), entry('opus','opus'),
                        entry('deepseek','configured',False)]
        self.calls = []
        self.graph = {'reason':'Purposeful graph', 'nodes':[node('scout'), node('write',['scout'],read_only=False), node('review',['write'])]}
        self.job = dict(chat_id='chat', status='running', plan={})
        self.plan = dict(turn_id='turn', exec_engine='codex', available_engines=['codex','sonnet','opus','deepseek'],
                         quotas={'codex': {'seven_day_pct': 20, 'seven_day_reset': time.time()+3*86400}}, domain='NAS')
        self.web = types.SimpleNamespace(Q=Q, M=types.SimpleNamespace(STATE_DIR=self.root, ENGINE_DISPLAY={}),
                                         JOBS_LOCK=threading.RLock(), execute_agent_turn=self.execute,
                                         _push_job_step=lambda *a:None)
        for name, replacement in [('catalog',lambda **kw:{'models':copy.deepcopy(self.entries)}),('resolve',self.resolve)]:
            p = patch.object(R.MODELS,name,side_effect=replacement)
            p.start()
            self.addCleanup(p.stop)

    def resolve(self, model_id, effort=None, quotas=None):
        item = next((e for e in self.entries if e['id']==model_id), None)
        if not item or not item['available'] or (effort and effort not in item['efforts']):
            raise ValueError('Invalid model/effort')
        return dict(item, effort=effort or item['default_effort'])

    def execute(self, engine, prompt, instruction, cwd, images, plan, folder_id, job):
        self.calls.append(dict(engine=engine,prompt=prompt,plan=copy.deepcopy(plan),node=job['node_id']))
        return dict(text=json.dumps(self.graph) if plan.get('no_tools') else 'Evidence '+str(job['node_id']),
                    agent_used=engine+':actual',rc=0,tools=[],plan=plan,elapsed_s=0)

    def run_turn(self, text='Inspect and improve'):
        return R.run_turn(self.web,'codex','Main identity codex',text,str(self.root),[],self.plan,'nas',self.job)

    def saved(self):
        return json.loads((self.root/'workflows/turn.json').read_text())

    def test_full_graph_handoffs_sink_response_and_authoritative_identity(self):
        result = self.run_turn()
        self.assertEqual(result['rc'],0)
        self.assertIn('Evidence review',result['text'])
        self.assertNotIn('Evidence scout',result['text'])
        self.assertNotIn('Evidence write',result['text'])
        self.assertEqual([c['node'] for c in self.calls],[None,'scout','write','review','sintese'])
        self.assertIn('pasta='+str(self.root),self.calls[0]['prompt'])
        self.assertIn('domínio=NAS',self.calls[0]['prompt'])
        self.assertIn('Evidence scout',self.calls[2]['prompt'])
        self.assertIn('IDENTIDADE AUTORITATIVA',self.calls[2]['prompt'])
        self.assertIn('resposta será mostrada ao utilizador',self.calls[3]['prompt'])
        self.assertEqual(self.saved()['planning_source'],'coordinator')
        self.assertEqual(self.saved()['nodes'][0]['result']['text'],'Evidence scout')

    def test_repeated_single_node_success_skips_planner_with_local_feedback(self):
        self.graph = {'nodes': [node('implement', read_only=False)]}
        for i in range(3):
            self.plan['turn_id'] = 'turn-' + str(i)
            self.assertEqual(self.run_turn()['rc'], 0)
        self.calls.clear()
        self.plan['turn_id'] = 'turn-local'
        result = self.run_turn()
        self.assertEqual([c['node'] for c in self.calls], ['implement'])
        self.assertEqual(result['plan']['workflow']['planning_source'], 'local_feedback')
        self.assertEqual(result['plan']['efficiency']['calls'], 1)
        self.assertIsNone(result['plan']['efficiency']['total_savings_tokens'])

    def test_cheap_planner_and_scoped_workers_keep_original_context_available(self):
        self.entries[1]['model'] = 'gpt-6-luna'
        self.plan.update(model_id='codex:gpt-sol', automatic_model=True)
        self.graph = {'nodes': [node('answer'), node('write', read_only=False)]}
        history = 'OLD_HISTORY_SENTINEL' + ' historic detail' * 2000
        result = R.run_turn(self.web, 'codex', history, 'Qual a quota? Corrige o menu.',
                            str(self.root), [], self.plan, 'nas', self.job)
        self.assertEqual(result['rc'], 0)
        self.assertEqual(self.calls[0]['plan']['model_id'], 'codex:gpt-luna')
        self.assertEqual(self.calls[1]['plan']['model_id'], 'codex:gpt-luna')
        self.assertEqual(self.calls[2]['plan']['model_id'], 'codex:gpt-sol')
        self.assertNotIn('OLD_HISTORY_SENTINEL', self.calls[2]['prompt'])
        context = pathlib.Path(self.calls[2]['plan']['workflow_context_path'])
        self.assertEqual(context.read_text(), history)
        self.assertIn(str(context), self.calls[2]['prompt'])
        self.assertIn('Evidence answer', result['text'])
        self.assertIn('Evidence write', result['text'])

    def test_completed_workflow_does_not_call_any_model_on_resume(self):
        self.run_turn()
        self.calls.clear()
        self.job = dict(chat_id='chat',status='running',plan={})
        result = self.run_turn()
        self.assertEqual(result['rc'],0)
        self.assertEqual(self.calls,[])

    def test_resume_moves_unfinished_work_off_an_engine_without_quota(self):
        self.graph['nodes'][1]['model_id'] = 'codex:gpt-sol'
        original = self.web.execute_agent_turn
        self.web.execute_agent_turn = lambda *a, **kw: (dict(text='boom', agent_used='codex', rc=1)
                                                        if kw['job']['node_id'] == 'write' else original(*a, **kw))
        self.run_turn()
        self.assertEqual(self.saved()['nodes'][1]['status'], 'failed')
        self.web.execute_agent_turn = original
        self.calls.clear()
        self.entries = [e for e in self.entries if e['engine'] != 'codex']
        self.plan.update(exec_engine='sonnet', available_engines=['sonnet', 'opus', 'deepseek'])
        self.job = dict(chat_id='chat', status='running', plan={})
        result = self.run_turn()
        self.assertEqual(result['rc'], 0)
        nodes = {n['id']: n for n in self.saved()['nodes']}
        self.assertEqual(nodes['scout']['engine'], 'codex')  # finished work keeps its history
        self.assertEqual((nodes['write']['engine'], nodes['write']['status']), ('sonnet', 'done'))
        self.assertEqual({c['node']: c['engine'] for c in self.calls}, {'write': 'sonnet', 'review': 'sonnet', 'sintese': 'sonnet'})

    def test_resume_failed_path_keeps_completed_research_and_failed_output(self):
        original = self.web.execute_agent_turn
        def failing(*args,**kwargs):
            if kwargs['job']['node_id']=='write':
                return dict(text='Build error details',agent_used='actual',rc=1)
            return original(*args,**kwargs)
        self.web.execute_agent_turn = failing
        result = self.run_turn()
        self.assertEqual(result['rc'],1)
        self.assertIn('Build error details',result['text'])
        saved = self.saved()
        self.assertEqual(saved['nodes'][1]['result']['text'],'Build error details')
        self.assertEqual(saved['nodes'][2]['status'],'blocked')
        self.assertEqual(self.job['children'][2]['status'],'failed')
        self.calls.clear()
        self.web.execute_agent_turn = original
        self.job = dict(chat_id='chat',status='running',plan={})
        result = self.run_turn()
        self.assertEqual(result['rc'],0)
        self.assertEqual([c['node'] for c in self.calls],['write','review','sintese'])

    def test_pinned_turn_model_overrides_planner_choice_on_writing_nodes(self):
        self.plan.update(model_id='codex:gpt-luna', effort='high')
        self.graph = {'nodes':[node('scout',engine='sonnet'), node('write',['scout'],engine='opus',read_only=False)]}
        self.run_turn()
        call = next(c for c in self.calls if c['node']=='write')
        self.assertEqual((call['engine'], call['plan']['model_id'], call['plan']['effort']), ('codex','codex:gpt-luna','high'))
        self.assertEqual(self.saved()['nodes'][1]['model_id'], 'codex:gpt-luna')

    def test_same_family_different_model_does_not_inherit_wrong_selection(self):
        self.plan.update(exec_engine='claude-sonnet',model_id='sonnet:sonnet',effort='high',automatic_model=True)
        self.graph = {'nodes':[node('write',engine='opus',read_only=False)]}
        self.run_turn()
        call = next(c for c in self.calls if c['node']=='write')
        self.assertEqual(call['engine'],'opus')
        self.assertEqual(call['plan']['model_id'],'opus:opus')

    def test_review_findings_return_to_the_maker_in_the_same_turn(self):
        replies = {'review': 'Veredicto: NÃO APROVADO. Não tinha shell para validar. A corrigir: linha 4.'}
        base = self.execute
        def execute(engine, prompt, instruction, cwd, images, plan, folder_id, job):
            result = base(engine, prompt, instruction, cwd, images, plan, folder_id, job)
            return dict(result, text=replies.get(job['node_id'], result['text']))
        self.web.execute_agent_turn = execute
        result = self.run_turn()
        self.assertEqual([c['node'] for c in self.calls], [None, 'scout', 'write', 'review', 'entrega', 'sintese'])
        closing = self.calls[-2]
        self.assertFalse(closing['plan']['read_only'])
        self.assertIn('NÃO APROVADO', closing['prompt'])       # the findings are its work list
        self.assertIn('CONTRATO DE ENTREGA', closing['prompt'])
        self.assertIn('Inspect and improve', closing['prompt'])  # every node keeps the original request
        self.assertEqual(result['rc'], 0)
        self.assertIn('Evidence entrega', result['text'])
        self.assertNotIn('NÃO APROVADO', result['text'])
        self.assertEqual([n['id'] for n in self.saved()['nodes']][-2:], ['entrega', 'sintese'])
        self.calls.clear()                                       # resume: finished plan, no new model call
        self.assertIn('Evidence entrega', self.run_turn()['text'])
        self.assertEqual(self.calls, [])

    def test_clean_review_is_delivered_without_an_extra_pass(self):
        self.run_turn()
        self.assertEqual([c['node'] for c in self.calls], [None, 'scout', 'write', 'review', 'sintese'])

    def test_change_request_never_ends_in_a_read_only_plan(self):
        self.graph = {'nodes': [node('scout')]}
        result = self.run_turn('Implementa e valida as licenças')
        self.assertEqual([c['node'] for c in self.calls], [None, 'scout', 'entrega', 'sintese'])
        self.assertFalse(self.calls[-2]['plan']['read_only'])
        self.assertIn('Evidence entrega', result['text'])

    def test_deepseek_without_daily_budget_is_not_planned(self):
        self.entries.append(entry('deepseek', 'flash-api'))
        self.plan['quotas']['deepseek'] = {'available': True, 'budget': {'available': False}}
        self.run_turn()
        self.assertNotIn('deepseek', {c['engine'] for c in self.calls})
        self.assertNotIn('"deepseek"', self.calls[0]['prompt'].split('Usa apenas estes motores:')[1].split('.')[0])

    def test_planned_model_and_effort_persist_across_load(self):
        self.graph = {'nodes':[node('review',model_id='codex:gpt-luna',effort='high')]}
        self.run_turn('Explain the design')
        self.assertEqual(self.calls[-1]['plan']['model_id'],'codex:gpt-luna')
        self.assertEqual(self.calls[-1]['plan']['effort'],'high')
        restored = WF.Workflow.load(self.root/'workflows/turn.json').snapshot()['nodes'][0]
        self.assertEqual(restored['model_id'],'codex:gpt-luna')
        self.assertEqual(restored['effort'],'high')

    def test_malformed_model_choice_recovers_to_valid_catalogue(self):
        self.graph = {'nodes':[node('write',read_only=False,model_id='codex:fake',effort='ultra')]}
        result = self.run_turn()
        self.assertEqual(result['rc'],0)
        self.assertEqual(self.calls[-1]['plan']['model_id'],'codex:gpt-sol')
        self.assertIn('inválida corrigida',self.saved()['reason'])

    def test_no_available_provider_is_not_reinserted(self):
        for e in self.entries:
            e['available']=False
        with self.assertRaisesRegex(RuntimeError,'Nenhum motor'):
            self.run_turn()
        self.assertEqual(self.calls,[])

    def test_catalogue_engine_outside_allowed_families_not_used_for_readonly(self):
        self.plan.update(exec_engine='deepseek',available_engines=['deepseek'])
        result = self.run_turn()
        self.assertEqual(result['rc'],0)
        self.assertEqual([c['node'] for c in self.calls],['implement'])
        self.assertEqual(self.calls[0]['engine'],'deepseek')
        self.assertFalse(self.calls[0]['plan']['read_only'])

    def test_cancellation_during_planning_launches_no_work(self):
        def cancel(*args,**kwargs):
            self.job['status']='stopped'
            return dict(text='{}',agent_used='actual',rc=0)
        self.web.execute_agent_turn = cancel
        with self.assertRaisesRegex(RuntimeError,'Interrompido'):
            self.run_turn()
        self.assertEqual(self.job['children'][0]['status'],'stopped')
        self.assertFalse((self.root/'workflows/turn.json').exists())

    def test_saved_turn_objective_cannot_change_on_retry(self):
        self.run_turn()
        with self.assertRaisesRegex(ValueError,'pedido guardado'):
            self.run_turn('Different request')

    def test_sandbox_capability_failure_routes_reads_to_safe_adapter(self):
        self.plan['available_engines'] = ['codex','deepseek']
        for item in self.entries:
            if item['engine'] == 'codex':
                item['read_only_enforced'] = False
            elif item['engine'] == 'deepseek':
                item['read_only_enforced'] = True
        result = self.run_turn()
        self.assertEqual(result['rc'],0)
        self.assertEqual([c['engine'] for c in self.calls],['deepseek','deepseek','codex','deepseek','deepseek'])
        self.assertEqual([c['plan']['read_only'] for c in self.calls],[True,True,False,True,True])

    def test_orchestrator_synthesis_speaks_first_and_reports_follow(self):
        result = self.run_turn()
        synth = self.calls[-1]
        self.assertEqual(synth['node'], 'sintese')
        self.assertTrue(synth['plan']['read_only'])
        self.assertIn('És o Maestro', synth['prompt'])
        for done in ('Evidence scout', 'Evidence write', 'Evidence review'):
            self.assertIn(done, synth['prompt'])           # it sees every agent's result
        self.assertTrue(result['text'].startswith('Evidence sintese'))
        self.assertIn('Evidence review', result['text'])   # the final report stays visible below
        self.assertNotIn('Evidence scout', result['text'])
        self.calls.clear()
        self.job = dict(chat_id='chat', status='running', plan={})
        self.assertTrue(self.run_turn()['text'].startswith('Evidence sintese'))
        self.assertEqual(self.calls, [])                   # finished turn: no new synthesis on resume


if __name__=='__main__':unittest.main()
