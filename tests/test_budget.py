import datetime as dt
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_budget as B
import maestro_queue as Q


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name)/'budget.json'
        self.now = dt.datetime(2026, 11, 20, 12, tzinfo=B.TZ)
        self.payload = dict(model='deepseek-flash', messages=[dict(role='user', content='Olá')], max_tokens=100)

    def request(self, send=lambda: {}, **kwargs):
        return B.request(self.payload, send, path=self.path, now=kwargs.get('now', self.now))

    def test_cycles_reset_on_twentieth_and_follow_actual_day_count(self):
        for date, start, end, days in [('2026-11-19','2026-10-20','2026-11-20',31),
                                       ('2026-11-20','2026-11-20','2026-12-20',30),
                                       ('2026-01-01','2025-12-20','2026-01-20',31),
                                       ('2026-02-20','2026-02-20','2026-03-20',28)]:
            now = dt.datetime.fromisoformat(date).replace(tzinfo=B.TZ)
            s = B.status(self.path, now)
            self.assertEqual((s['cycle_start'],s['reset_at']),(start,end))
            self.assertAlmostEqual(s['daily_eur'],15/days,places=6)

    def test_settlement_and_persistence(self):
        self.request(lambda: {'usage': {'prompt_tokens': 10, 'completion_tokens': 20}})
        s = B.status(self.path, self.now)
        self.assertAlmostEqual(s['spent_eur'], B.EUR_PER_USD*(10*.3+20*1.2)/1e6, places=6)
        self.assertGreater(B.status(self.path, self.now)['today_eur'],0)

    def test_timeout_keeps_reservation(self):
        with self.assertRaises(TimeoutError):
            self.request(lambda: (_ for _ in ()).throw(TimeoutError()))
        self.assertGreater(B.status(self.path,self.now)['today_eur'], 0)

    def test_daily_and_cycle_limits_block_before_network(self):
        for spend in [ {'2026-11-20': .5}, {'2026-11-19': 15} ]:
            now = self.now if '2026-11-20' in spend else dt.datetime(2026,11,19,12,tzinfo=B.TZ)
            with B.ledger(self.path) as d: d['days'] = spend
            with self.assertRaises(RuntimeError):
                self.request(lambda: self.fail('network called'), now=now)

    def test_next_day_and_cycle_restore_allowance(self):
        with B.ledger(self.path) as d: d['days'] = {'2026-11-19': 15, '2026-11-20': .5}
        self.assertTrue(B.status(self.path, self.now+dt.timedelta(days=1))['available'])
        self.assertEqual(B.status(self.path,self.now)['spent_eur'],.5)

    def test_corruption_and_unknown_prices_fail_closed(self):
        self.path.write_text('broken')
        with self.assertRaises(ValueError): self.request()
        self.path.unlink()
        self.payload['model']='unknown'
        with self.assertRaises(RuntimeError): self.request()
        self.assertTrue(B.status(self.path, self.now)['cli_allowed'])

    def test_yellow_and_red_need_justification_and_keep_monthly_cap(self):
        with B.ledger(self.path) as d:
            d['days'] = {'2026-11-20': .5}
        with self.assertRaises(RuntimeError):
            self.request()
        with self.assertRaises(ValueError):
            B.request(self.payload, lambda: {}, self.path, self.now, necessity='essential')
        B.request(self.payload, lambda: {}, self.path, self.now, necessity='essential', reason='Sem alternativa adequada')
        with B.ledger(self.path) as d:
            self.assertEqual(d['exceptions'][-1]['necessity'], 'essential')
            d['days']['2026-11-20'] = .8
        with self.assertRaises(RuntimeError):
            B.request(self.payload, lambda: self.fail('network'), self.path, self.now, necessity='essential', reason='Continuidade')
        B.request(self.payload, lambda: {}, self.path, self.now, necessity='critical', reason='Recuperar dados em risco')
        with B.ledger(self.path) as d:
            d['days'] = {'2026-11-21': 14.8, '2026-11-20': .3}
        with self.assertRaises(RuntimeError):
            B.request(self.payload, lambda: self.fail('network'), self.path, self.now, necessity='critical', reason='Incidente')

    def test_search_fixed_fee_is_reserved_and_settled(self):
        B.request(self.payload, lambda: {'usage': {'prompt_tokens': 0, 'completion_tokens': 0}},
                  self.path, self.now, request_fee_usd=.00075)
        self.assertAlmostEqual(B.status(self.path, self.now)['today_eur'], .00075*B.EUR_PER_USD, places=6)

    def test_critical_queue_can_admit_deepseek_redline(self):
        task = dict(chat_id='x', engine='auto', text='Serviço indisponível urgente, corrige agora')
        plan = dict(dim_level=3, available_engines=[], quotas=dict(deepseek=dict(
            available=False, budget=dict(available=False, reserve_available=False,
                                         critical_available=True, cli_allowed=True))))
        self.assertEqual(Q.decide(task, plan, [])[0], 'deepseek')

    def test_concurrent_process_style_reservations_cannot_overspend(self):
        self.payload['messages'][0]['content']='x'*120000
        failures=[]
        def call():
            try: self.request()
            except RuntimeError: failures.append(True)
        threads=[threading.Thread(target=call) for _ in range(25)]
        for t in threads:t.start()
        for t in threads:t.join(3)
        self.assertTrue(failures)
        self.assertLessEqual(B.status(self.path,self.now)['today_eur'],.5)


class SubscriptionSlotsTests(unittest.TestCase):
    def test_distinct_files_in_same_project_can_write_together(self):
        slots = Q.ExecutionSlots()
        with slots.hold(False, engine='codex', scope=['/project/a.py']):
            with slots.hold(False, engine='haiku', scope=['/project/b.py', '/project/c.py']):
                self.assertEqual(len(slots.active), 2)
        self.assertTrue(Q.scopes_overlap(['/project/a.py'], ['/project']))
        self.assertTrue(Q.scopes_overlap(['/project/a.py'], ['/project/b.py', '/project/a.py']))
        self.assertTrue(Q.scopes_overlap(None, ['/project/a.py']))

    def test_three_providers_can_write_independent_projects(self):
        slots=Q.ExecutionSlots()
        with slots.hold(False,engine='agy',scope='/gemini'):
            with slots.hold(False,engine='codex',scope='/codex'):
                with slots.hold(False,engine='haiku',scope='/claude'):
                    self.assertEqual(len(slots.active),3)

    def test_same_subscription_or_overlapping_files_wait(self):
        for engine,scope in [('opus','/other'),('codex','/project/subfolder')]:
            slots=Q.ExecutionSlots(); waiting=threading.Event(); entered=threading.Event()
            def run():
                with slots.hold(False,engine=engine,scope=scope,waiting=waiting.set):entered.set()
            with slots.hold(False,engine='haiku',scope='/project'), slots.hold(False,engine='sonnet',scope='/p2'):
                thread=threading.Thread(target=run);thread.start()
                self.assertTrue(waiting.wait(2));self.assertFalse(entered.is_set())
            thread.join(2);self.assertTrue(entered.is_set())

    def test_under_pace_subscriptions_precede_paid_api(self):
        task=dict(chat_id='x',text='Analisa os logs do servidor',engine='auto')
        plan=dict(dim_level=1,exec_engine='deepseek',available_engines=['deepseek','claude-sonnet'],
                  quotas={'deepseek':{'available':True},'claude':{'five_hour_pct':20,'seven_day_pct':10,'pace_target_pct':40}})
        self.assertEqual(Q.decide(task,plan,[])[0],'claude-sonnet')

    def test_complex_work_can_use_another_capable_subscription(self):
        task=dict(chat_id='x',text='Analisa em paralelo os logs do servidor',engine='auto')
        plan=dict(dim_level=3,exec_engine='codex',available_engines=['codex','claude-sonnet'],
                  quotas={'codex':{'five_hour_est_pct':20,'seven_day_pct':10},
                          'claude':{'five_hour_pct':20,'seven_day_pct':10}})
        self.assertEqual(Q.decide(task,plan,[dict(plan={'exec_engine':'codex'})])[0],'claude-sonnet')
