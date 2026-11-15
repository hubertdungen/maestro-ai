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
        self.assertEqual([c['node'] for c in self.calls],[None,'scout','write','review'])
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
        self.assertEqual([c['node'] for c in self.calls],['write','review'])

    def test_same_family_different_model_does_not_inherit_wrong_selection(self):
        self.plan.update(exec_engine='claude-sonnet',model_id='sonnet:sonnet',effort='high')
        self.graph = {'nodes':[node('write',engine='opus',read_only=False)]}
        self.run_turn()
        call = next(c for c in self.calls if c['node']=='write')
        self.assertEqual(call['engine'],'opus')
        self.assertEqual(call['plan']['model_id'],'opus:opus')

    def test_planned_model_and_effort_persist_across_load(self):
        self.graph = {'nodes':[node('review',model_id='codex:gpt-luna',effort='high')]}
        self.run_turn()
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
        self.assertEqual([c['engine'] for c in self.calls],['deepseek','deepseek','codex','deepseek'])
        self.assertEqual([c['plan']['read_only'] for c in self.calls],[True,True,False,True])


if __name__=='__main__':unittest.main()
