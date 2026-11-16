import copy
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_queue as Q
import maestro_web as W


def plan(**overrides):
    p = dict(domain='NAS', dimension='Média', dim_level=2, agents=['Codex'], reason='test',
             exec_engine='codex', available_engines=['codex', 'deepseek'], maestro_name='Codex',
             quotas={'codex': {'five_hour_est_pct': 28, 'seven_day_pct':20, 'seven_day_reset':time.time()+3*86400},
                     'claude': {'seven_day_pct':100, 'pace_target_pct':80},
                     'antigravity': {'limited':True}, 'deepseek': {'available':True, 'balance_text':'13 USD'}})
    p.update(overrides)
    return p


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(chat_id='chat', text='Corrige o menu', engine='auto')
        self.running = [dict(chat_id='chat', plan={'exec_engine':'codex'})]

    def test_two_tries_same_engine_then_others_then_exhausted(self):
        d = lambda f: Q.decide(dict(self.task, failed_engines=f), plan(dim_level=1), [])
        self.assertEqual(d(['codex']), ('codex', 'ready'))          # 2nd try, same engine
        self.assertEqual(d(['codex', 'codex']), ('deepseek', 'economy'))  # then another
        self.assertEqual(d(['codex', 'codex', 'deepseek']), (None, 'exhausted'))

    def test_writes_and_ambiguous_followups_wait(self):
        for text in ['Corrige o menu', 'Agora faz isso', 'Analisa o resultado anterior', 'Independentemente analisa e altera o código']:
            t = dict(self.task, text=text)
            self.assertEqual(Q.decide(t, plan(), self.running), (None, 'dependency'))

    def test_independent_readonly_uses_different_free_provider(self):
        t = dict(self.task, text='Analisa em paralelo os logs do Lectoria')
        self.assertEqual(Q.decide(t, plan(), self.running), ('deepseek', 'parallel'))

    def test_two_runs_per_engine_then_busy(self):
        t = dict(self.task, text='Analisa em paralelo os logs', parallel_ok=True)
        two = self.running * 2
        p = plan(dim_level=3)
        self.assertEqual(Q.decide(t, p, self.running), ('codex', 'parallel'))  # 2nd run allowed when no other plan
        self.assertEqual(Q.decide(t, p, two), (None, 'engine_busy'))           # 3rd refused

    def test_prefers_idle_equivalent_plan_before_second_run(self):
        t = dict(self.task, text='Analisa em paralelo os logs', parallel_ok=True)
        p = plan(dim_level=3, available_engines=['codex', 'sonnet'])
        p['quotas']['claude'] = {'five_hour_pct': 10, 'seven_day_pct': 10, 'pace_target_pct': 80}
        self.assertEqual(Q.decide(t, p, self.running)[0], 'sonnet')

    def test_complex_work_keeps_specialist(self):
        t = dict(self.task, text='Analisa em paralelo os logs')
        self.assertEqual(Q.decide(t, plan(dim_level=3), self.running * 2), (None, 'engine_busy'))

    def test_cheap_short_task_prefers_spare_included_quota(self):
        self.assertEqual(Q.decide(self.task, plan(dim_level=1), []), ('codex', 'ready'))

    def test_quota_limits_and_manual_choice(self):
        p = plan(); p['quotas']['codex']['five_hour_est_pct'] = 100
        self.assertEqual(Q.decide(self.task, p, []), ('deepseek','economy'))
        self.assertEqual(Q.decide(dict(self.task,engine='codex'),p,[]), (None,'quota'))

    def test_images_not_assigned_to_deepseek(self):
        p=plan(available_engines=['deepseek'])
        self.assertEqual(Q.decide(dict(self.task, images=['x.png']),p,[]), (None,'quota'))

    def test_parallel_capacity(self):
        self.assertEqual(Q.decide(self.task,plan(),self.running*Q.MAX_PARALLEL_AGENTS),(None,'capacity'))

    def test_distinct_project_can_supply_independence(self):
        t = dict(self.task, text='Analisa os logs do Lectoria', separate_project=True)
        self.assertEqual(Q.decide(t,plan(),self.running),('deepseek','parallel'))

    def test_large_context_can_use_economical_provider(self):
        t = dict(self.task, estimated_input_tokens=7000)
        self.assertEqual(Q.decide(t,plan(),[]),('codex','ready'))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.queue = Q.TurnQueue(self.root/'queue.json')
        for key,value in [('CHATS_DIR',self.root/'chats'),('TURN_QUEUE',self.queue),('ACTIVE_JOBS',{})]:
            p=patch.object(W,key,value);p.start();self.addCleanup(p.stop)
        W.CHATS_DIR.mkdir()
        p=patch.object(Q,'triage_independent',return_value=False);p.start();self.addCleanup(p.stop)  # no real Haiku calls
        # Title workers have their own async tests; queue fixtures must not outlive storage.
        p=patch.object(W,'schedule_chat_title');p.start();self.addCleanup(p.stop)
        p=patch.object(W,'ensure_all_dirs');p.start();self.addCleanup(p.stop)
        p=patch.object(W,'get_folder_map', return_value={'nas':{'name':'NAS','cwd':str(self.root)}});p.start();self.addCleanup(p.stop)
        W.save_chat(dict(id='chat', project='nas', engine='auto', messages=[]))
        self.classify=patch.object(W.M,'classify_task',side_effect=lambda *args, **kwargs:plan());self.classify.start();self.addCleanup(self.classify.stop)

    def enqueue(self,text,**kw):
        return W.enqueue_turn(dict(id='chat', text=text, project='nas', engine='auto', **kw))

    def wait_done(self):
        deadline=time.time()+3
        while time.time()<deadline:
            if not any(j['status']=='running' for j in W.ACTIVE_JOBS.values()): return
            time.sleep(.01)
        self.fail('worker did not finish')

    def test_durable_deduplicated_submission(self):
        self.enqueue('A',request_id='same');self.enqueue('A',request_id='same')
        self.assertEqual(W.load_chat('chat')['messages'], [])
        self.assertEqual(len(Q.TurnQueue(self.queue.path).pending('chat')),1)

    def test_waiting_requests_stay_out_of_history_until_admission(self):
        image = str(self.root / 'example.png')
        pathlib.Path(image).write_bytes(b'fixture')
        response = self.enqueue('Primeiro', images=[image])
        self.assertEqual(response['chat']['messages'], [])
        self.assertEqual(response['queue'][0]['images'], [image])
        self.enqueue('Segundo')
        self.queue.update(self.queue.tasks[0]['id'], status='interrupted')
        W.schedule_turns_once()
        self.assertEqual(W.chat_activity_snapshot('chat')['chat']['messages'], [])
        self.queue.update(self.queue.tasks[0]['id'], status='queued')
        result = dict(text='Feito', agent_used='Test', elapsed_s=0, plan={}, tools=[])
        with patch.object(W, 'execute_agent_turn', return_value=result):
            W.schedule_turns_once(); self.wait_done()
        messages = W.chat_activity_snapshot('chat')['chat']['messages']
        self.assertEqual([m['text'] for m in messages], ['Primeiro', 'Feito'])
        self.assertEqual(messages[0]['admission']['reason'], 'ready')
        self.assertTrue(messages[0]['admission']['engine'])
        self.assertTrue(self.queue.tasks[0]['started_at'])

    def test_legacy_waiting_message_is_filtered_then_admitted_once(self):
        self.enqueue('Antigo')
        task = self.queue.tasks[0]
        chat = W.load_chat('chat')
        chat['messages'].append(dict(role='user', text='Antigo', turn_id=task['id']))
        W.save_chat(chat)
        self.assertEqual(W.chat_activity_snapshot('chat')['chat']['messages'], [])
        result = dict(text='Feito', agent_used='Test', elapsed_s=0, plan={}, tools=[])
        with patch.object(W, 'execute_agent_turn', return_value=result):
            W.schedule_turns_once(); self.wait_done()
        self.assertEqual([m['text'] for m in W.load_chat('chat')['messages']], ['Antigo', 'Feito'])

    def test_cancelled_legacy_request_never_appears_in_history(self):
        self.enqueue('Cancelar')
        task = self.queue.tasks[0]
        chat = W.load_chat('chat')
        chat['messages'].append(dict(role='user', text='Cancelar', turn_id=task['id']))
        W.save_chat(chat)
        self.queue.update(task['id'], status='cancelled')
        snapshot = W.chat_activity_snapshot('chat')
        self.assertEqual(snapshot['chat']['messages'], [])
        self.assertEqual(snapshot['queue'], [])

    def test_restart_auto_resumes_running_task_twice_then_needs_manual_retry(self):
        self.enqueue('A'); task=self.queue.tasks[0]
        for expected in ('queued','queued','interrupted'):
            self.queue.update(task['id'],status='running')
            recovered=Q.TurnQueue(self.queue.path);recovered.recover();self.queue=recovered
            self.assertEqual(recovered.pending('chat')[0]['status'],expected)

    def test_sequential_dependency_gets_previous_answer(self):
        prompts=[]
        def execute(*args,**kwargs):
            prompts.append(args[1]);return dict(text='Resposta '+args[2],agent_used='Codex',elapsed_s=0,plan={},tools=[])
        with patch.object(W,'execute_agent_turn',side_effect=execute):
            self.enqueue('Primeiro'); self.enqueue('Segundo')
            W.schedule_turns_once();self.wait_done()
            W.schedule_turns_once();self.wait_done()
        self.assertIn('Resposta Primeiro',prompts[1])
        self.assertNotIn('Segundo',prompts[0])
        self.assertEqual([m['text'] for m in W.load_chat('chat')['messages']],['Primeiro','Resposta Primeiro','Segundo','Resposta Segundo'])

    def test_parallel_completion_order_preserves_pairs_and_both_answers(self):
        gate=threading.Event();second_done=threading.Event()
        def execute(*args,**kwargs):
            if args[2]=='Primeiro': gate.wait(2)
            else: second_done.set()
            return dict(text='Resposta '+args[2],agent_used='Test',elapsed_s=0,plan={},tools=[])
        with patch.object(W,'execute_agent_turn',side_effect=execute):
            self.enqueue('Primeiro');W.schedule_turns_once()
            self.enqueue('Analisa em paralelo logs do Lectoria');W.schedule_turns_once()
            self.assertTrue(second_done.wait(1))
            gate.set();self.wait_done()
        messages=W.load_chat('chat')['messages']
        self.assertEqual([m['role'] for m in messages],['user','assistant','user','assistant'])
        self.assertEqual(messages[1]['text'],'Resposta Primeiro')
        self.assertEqual(len(messages),4)

    def test_independent_request_bypasses_blocked_followup_and_enforces_readonly(self):
        gate = threading.Event(); parallel = threading.Event(); plans = []
        def execute(*args, **kwargs):
            if args[2] == 'Primeiro':
                gate.wait(3)
            else:
                plans.append(args[5]);parallel.set()
            return dict(text='Resposta',agent_used='Test',elapsed_s=0,plan={},tools=[])
        with patch.object(W,'execute_agent_turn',side_effect=execute):
            try:
                self.enqueue('Primeiro'); W.schedule_turns_once()
                self.enqueue('Agora usa o resultado anterior')
                self.enqueue('Explica como funciona o arquivo de conversas')
                W.schedule_turns_once()
                self.assertTrue(parallel.wait(2))
                self.assertEqual(self.queue.tasks[1]['status'], 'queued')
                self.assertTrue(plans[0]['read_only'])
                self.assertEqual(plans[0]['queue_reason'], 'parallel')
            finally:
                gate.set();self.wait_done()

    def test_exception_releases_queue(self):
        with patch.object(W,'execute_agent_turn',side_effect=RuntimeError('fixture')):
            self.enqueue('A');W.schedule_turns_once();self.wait_done()
        self.assertIn('Falha',W.load_chat('chat')['messages'][1]['text'])
        self.assertEqual((self.queue.tasks[0]['status'],self.queue.tasks[0]['auto_retries']),('queued',1))  # auto-retry, not stuck

    def test_same_chat_distinct_files_write_in_parallel_with_bounded_prompts(self):
        for name in ('first.py', 'second.py'):
            (self.root / name).write_text('# fixture')
        gate = threading.Event(); parallel = threading.Event(); prompts = []
        def execute(*args, **kwargs):
            prompts.append(args[1])
            if 'first.py' in args[2]:
                gate.wait(3)
            else:
                parallel.set()
            return dict(text='Feito', agent_used='Test', elapsed_s=0, plan={}, tools=[])
        with patch.object(W, 'execute_agent_turn', side_effect=execute):
            try:
                self.enqueue('Corrige first.py'); W.schedule_turns_once()
                self.enqueue('Corrige second.py'); W.schedule_turns_once()
                self.assertTrue(parallel.wait(1))
                self.assertEqual(self.queue.tasks[1]['reason'], 'parallel_write')
                self.assertTrue(all('Altera apenas estes ficheiros' in p for p in prompts))
            finally:
                gate.set(); self.wait_done()

    def test_overlapping_or_unknown_write_scope_stays_queued(self):
        (self.root / 'first.py').write_text('# fixture')
        gate = threading.Event()
        def execute(*args, **kwargs):
            gate.wait(3)
            return dict(text='Feito', agent_used='Test', elapsed_s=0, plan={}, tools=[])
        with patch.object(W, 'execute_agent_turn', side_effect=execute):
            try:
                self.enqueue('Corrige first.py'); W.schedule_turns_once()
                self.enqueue('Melhora first.py')
                self.enqueue('Corrige o menu')
                W.schedule_turns_once()
                self.assertEqual([t['status'] for t in self.queue.tasks], ['running', 'queued', 'queued'])
            finally:
                gate.set(); self.wait_done()

    def test_stop_cancels_waiting_work(self):
        self.enqueue('A');W.stop_chat_job('chat')
        self.assertEqual(self.queue.pending('chat'),[])
        self.assertIsNone(W.get_job_snapshot('chat'))

    def test_pending_request_snapshot_keeps_polling(self):
        response = self.enqueue('A')
        self.assertFalse(response['running'])
        self.assertEqual(response['job']['status'], 'queued')
        self.assertEqual(response['job']['task_id'], response['queue'][0]['id'])
        self.assertGreater(response['activity_version'], 0)

    def test_snapshot_distinguishes_running_request_from_pending_followup(self):
        gate = threading.Event()
        def execute(*args, **kwargs):
            gate.wait(2)
            return dict(text='Resposta', agent_used='Test', elapsed_s=0, plan={}, tools=[])
        with patch.object(W, 'execute_agent_turn', side_effect=execute):
            try:
                self.enqueue('Primeiro'); W.schedule_turns_once()
                response = self.enqueue('Segundo')
                first, second = self.queue.tasks
                self.assertTrue(response['running'])
                self.assertEqual(response['job']['task_id'], first['id'])
                self.assertEqual([t['id'] for t in response['queue']], [second['id']])
                W.schedule_turns_once()
                self.assertEqual(self.queue.tasks[1]['status'], 'queued')
            finally:
                gate.set(); self.wait_done()
        response = W.chat_activity_snapshot('chat')
        self.assertFalse(response['running'])
        self.assertEqual(response['job']['status'], 'queued')
        self.assertEqual(response['job']['task_id'], second['id'])
        with patch.object(W, 'execute_agent_turn', side_effect=execute):
            W.schedule_turns_once(); self.wait_done()
        response = W.chat_activity_snapshot('chat')
        self.assertIsNone(response['job'])
        self.assertEqual(response['queue'], [])

    def test_steering_preserves_pending_queue(self):
        self.enqueue('A'); W.stop_chat_job('chat',reason='steer')
        self.assertEqual(len(self.queue.pending('chat')),1)

    def test_steer_becomes_first_forced_task_and_gets_a_reply(self):
        self.enqueue('A'); self.enqueue('B')
        W.TURN_QUEUE.update(self.queue.tasks[0]['id'], status='running')
        W.enqueue_turn(W.steer_prepare({'id': 'chat', 'text': 'virar', 'steer': True}))
        self.assertEqual(self.queue.tasks[0]['status'], 'stopped')
        pend = self.queue.pending('chat')
        self.assertEqual([t['text'] for t in pend][:2], ['virar', 'B'])
        self.assertTrue(pend[0]['force'] and pend[0]['steer'])

    def test_steer_keeps_interrupted_strategy_on_original_request(self):
        workflow = {'turn_id': 'before', 'status': 'running', 'nodes': [
            {'id': 'a', 'status': 'done'}, {'id': 'b', 'status': 'running'}]}
        W.save_chat(dict(id='chat', messages=[{'role': 'user', 'turn_id': 'before', 'text': 'Build'}]))
        snapshot = dict(task_id='before', plan=plan(), workflow=workflow)
        with patch.object(W, 'get_job_snapshot', return_value=snapshot), patch.object(W, 'stop_chat_job'):
            W.steer_prepare({'id': 'chat', 'text': 'Change'})
        saved = W.load_chat('chat')['messages'][0]['plan']
        self.assertEqual(saved['status'], 'interrupted')
        self.assertEqual([n['status'] for n in saved['workflow']['nodes']], ['done', 'interrupted'])
        self.assertEqual(workflow['nodes'][1]['status'], 'running')  # no mutation of live state
        self.assertEqual(saved['exec_engine'], 'codex')

    def test_failed_turn_retry_replaces_error_response(self):
        with patch.object(W,'execute_agent_turn',side_effect=RuntimeError('fixture')):
            self.enqueue('A');W.schedule_turns_once();self.wait_done()
        task=self.queue.tasks[0]
        self.queue.update(task['id'],status='queued')
        result=dict(text='Success',agent_used='Test',elapsed_s=0,plan={},tools=[])
        with patch.object(W,'execute_agent_turn',return_value=result):
            W.schedule_turns_once();self.wait_done()
        self.assertEqual([m['text'] for m in W.load_chat('chat')['messages']],['A','Success'])

    def test_failed_predecessor_blocks_dependent_followup(self):
        self.enqueue('A'); self.enqueue('Agora usa o resultado anterior')
        self.queue.update(self.queue.tasks[0]['id'],status='interrupted')
        W.schedule_turns_once()
        self.assertEqual(self.queue.tasks[1]['status'],'queued')
        self.assertEqual(self.queue.tasks[1]['reason'],'dependency')
        self.assertEqual(self.queue.tasks[1]['blocked_by'],self.queue.tasks[0]['id'])
        self.assertFalse(W.chat_activity_snapshot('chat')['running'])


if __name__=='__main__':unittest.main()


class TriageTests(unittest.TestCase):
    def test_short_followups_and_images_always_wait(self):
        o = [dict(id='r', text='parar os contentores e desmontar o disco')]
        self.assertFalse(Q.triage_independent(dict(id='a', text='avança'), o, '/nonexistent'))
        self.assertFalse(Q.triage_independent(dict(id='b', text='uma frase bem comprida sobre outra coisa', images=['x.png']), o, '/nonexistent'))

    def test_failure_waits_and_is_not_cached(self):
        t = dict(id='c', text='uma frase bem comprida sobre outra coisa qualquer')
        self.assertFalse(Q.triage_independent(t, [dict(id='r', text='x')], '/nonexistent'))
        self.assertNotIn(('c', ('r',)), Q._TRIAGE_CACHE)


class SentinelFallbackTests(unittest.TestCase):
    def setUp(self):
        Q._TRIAGE_CACHE.clear()
        self.task = dict(id='sentinel-test', text='Analisa os registos completos do outro projeto Lectoria')
        self.others = [dict(id='running', text='Corrige os estilos do Maestro')]

    def run_triage(self, raw='', code=1, fallback=None):
        from types import SimpleNamespace
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=code, stdout=raw)):
            return Q.triage_independent(self.task, self.others, 'claude', fallback)

    def test_haiku_outage_uses_independent_provider(self):
        from unittest.mock import Mock
        backup = Mock(return_value='PARALELO')
        self.assertTrue(self.run_triage(fallback=backup))
        backup.assert_called_once()
        self.assertEqual(self.task['triage']['engine'], 'deepseek')

    def test_wait_is_a_valid_decision_not_an_outage(self):
        from unittest.mock import Mock
        backup = Mock(return_value='PARALELO')
        self.assertFalse(self.run_triage('ESPERAR', 0, backup))
        backup.assert_not_called()

    def test_invalid_or_negated_reply_cannot_admit_task(self):
        for text in ('NÃO PARALELO', 'ESPERAR ou PARALELO', '', 'quota unavailable'):
            Q._TRIAGE_CACHE.clear()
            self.assertFalse(self.run_triage(text, 0, lambda p: 'invalid'))
            self.assertFalse(Q._TRIAGE_CACHE)

    def test_both_providers_fail_then_retry_recovers(self):
        def fail(prompt):
            raise RuntimeError('unavailable')
        self.assertFalse(self.run_triage(fallback=fail))
        self.assertFalse(Q._TRIAGE_CACHE)
        self.assertTrue(self.run_triage(fallback=lambda p: 'PARALELO'))

    def test_text_edits_invalidate_cached_decision(self):
        self.assertTrue(self.run_triage('PARALELO', 0))
        self.task['text'] = 'Corrige também os estilos do mesmo projeto Maestro'
        self.assertFalse(self.run_triage('ESPERAR', 0))

    def test_server_fallback_uses_budgeted_adapter(self):
        import maestro_web as W
        with patch.object(W.M, '_extract_deepseek_key', return_value='fake'), patch.object(W.READONLY, '_request', return_value={'choices':[{'message':{'content':'PARALELO'}}]}) as req:
            self.assertEqual(W._sentinel_fallback('triage prompt'), 'PARALELO')
        self.assertEqual(req.call_args.args[3]['max_tokens'], 16)


class ParallelScopeTests(unittest.TestCase):
    def test_parallel_writer_admitted_by_the_scheduler_is_not_parked_behind_the_home_folder(self):
        import threading
        slots = Q.ExecutionSlots()
        home = Q.execution_scope({}, '/home/hubert')
        own = Q.execution_scope({'exec_scope': 'turno:abc'}, '/home/hubert')
        entered = threading.Event()
        with slots.hold(False, engine='sonnet', scope=home):
            def second():
                with slots.hold(False, engine='codex', scope=own):
                    entered.set()
            worker = threading.Thread(target=second, daemon=True)
            worker.start()
            self.assertTrue(entered.wait(2), 'the independent writer waited for the first one')
            worker.join(2)

    def test_same_turn_and_default_scopes_still_exclude_each_other(self):
        self.assertTrue(Q.scopes_overlap('turno:abc', 'turno:abc'))       # workflow siblings: one writer at a time
        self.assertTrue(Q.scopes_overlap('/home/hubert', '/home/hubert/Rumo'))
        self.assertEqual(Q.execution_scope({'write_paths': ['/a/b.py'], 'exec_scope': 'turno:x'}, '/a'), ['/a/b.py'])
        self.assertIn('repositório', Q.TRIAGE_PROMPT)
        self.assertTrue(Q.wants_changes('Implementa e valida'))
        self.assertFalse(Q.wants_changes('Explica como funciona a fila'))
