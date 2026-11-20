"""Spending controls must apply independently of model, images and queue force."""
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_queue as Q
import maestro_web as W

NOW = 1800000000
RESET = NOW + 5.5 * 86400


def quota(used=56, **extra):
    return dict(seven_day_pct=used, seven_day_reset=RESET, five_hour_est_pct=59, **extra)


def plan(**extra):
    result = dict(dim_level=2, exec_engine='codex', available_engines=['codex', 'deepseek'],
                  quotas={'codex': quota(), 'deepseek': {'available': True}})
    result.update(extra)
    return result


class BudgetTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(Q.time, 'time', return_value=NOW)
        p.start(); self.addCleanup(p.stop)
        self.task = dict(chat_id='x', text='Corrige o menu', engine='auto')

    def test_screenshot_usage_is_blocked_despite_five_hour_capacity(self):
        b = Q.weekly_budget(quota())
        self.assertAlmostEqual(b['pace_target_pct'], 200/7)
        self.assertTrue(b['pace_blocked'])
        self.assertGreater(b['pace_resume_at'], NOW)
        self.assertEqual(Q.decide(self.task, plan(), []), ('deepseek', 'economy'))

    def test_manual_force_and_images_do_not_override(self):
        for extras in [dict(engine='codex'), dict(force=True, engine='codex'), dict(images=['a.png'])]:
            self.assertEqual(Q.decide(dict(self.task, **extras), plan(), []), (None, 'daily_budget'))

    def test_complexity_does_not_spend_reserve(self):
        self.assertEqual(Q.decide(self.task, plan(dim_level=3), []), (None, 'daily_budget'))

    def test_explicit_critical_urgent_repair_can_use_reserve(self):
        t = dict(self.task, text='Corrige urgente a corrupção de dados: recuperar produção')
        self.assertEqual(Q.decide(t, plan(dim_level=3), []), ('codex', 'essential_reserve'))

    def test_critical_topic_without_urgent_repair_does_not_use_reserve(self):
        t = dict(self.task, text='Explica prevenção de perda de dados')
        self.assertEqual(Q.decide(t, plan(dim_level=3), []), (None, 'daily_budget'))

    def test_compatible_subscription_prevents_exception(self):
        p = plan(dim_level=3)
        p['available_engines'].append('claude-sonnet')
        p['quotas']['claude'] = dict(five_hour_pct=10, seven_day_pct=10, pace_target_pct=40)
        t = dict(self.task, text='Corrige urgente a perda de dados')
        self.assertEqual(Q.decide(t, p, []), ('claude-sonnet', 'ready'))

    def test_unknown_or_expired_week_is_conservative(self):
        for q in [{}, {'seven_day_pct': 0},
                  dict(seven_day_pct=0, seven_day_reset=NOW-1)]:
            self.assertTrue(Q.weekly_budget(q)['pace_blocked'])

    def test_first_day_releases_only_one_seventh(self):
        b = Q.weekly_budget(dict(seven_day_pct=0, seven_day_reset=NOW+7*86400))
        self.assertAlmostEqual(b['pace_target_pct'], 100/7)
        self.assertFalse(b['pace_blocked'])

    def test_exact_cap_blocks_and_next_day_releases(self):
        self.assertTrue(Q.weekly_budget(quota(200/7))['pace_blocked'])
        self.assertFalse(Q.weekly_budget(quota(200/7), NOW+86400)['pace_blocked'])

    def test_daily_cap_applies_even_with_accumulated_headroom(self):
        q = quota(30, daily_used_pct=15)
        q['seven_day_reset'] = NOW+2*86400
        self.assertTrue(Q.weekly_budget(q)['pace_blocked'])
        self.assertEqual(Q.weekly_budget(q)['pace_available_pct'], 0)

    def test_daily_samples_persist_and_reset_with_cycle_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp)/'usage.json'
            first = Q.observe_daily(p, 56, RESET, NOW)
            self.assertTrue(first['daily_measurement_partial'])
            self.assertEqual(first['daily_used_pct'], 0)
            self.assertEqual(Q.observe_daily(p, 60, RESET, NOW+60)['daily_used_pct'], 4)
            self.assertEqual(Q.observe_daily(p, 59, RESET, NOW+90)['daily_used_pct'], 4)
            self.assertEqual(Q.observe_daily(p, 62, RESET, NOW+86400)['daily_used_pct'], 0)
            self.assertEqual(Q.observe_daily(p, 0, RESET+7*86400, NOW+7*86400)['daily_used_pct'], 0)

    def test_readonly_api_is_not_used_for_implementation(self):
        p = plan()
        p['quotas']['deepseek']['budget'] = {'cli_allowed': False}
        self.assertEqual(Q.decide(self.task, p, []), (None, 'daily_budget'))
        t = dict(self.task, text='Explica como funciona o arquivo de conversas')
        self.assertEqual(Q.decide(t, p, []), ('deepseek', 'economy'))

    def test_runtime_manual_model_cannot_bypass(self):
        entry = dict(id='codex:test', engine='codex')
        with patch.object(W.MODELS, 'catalog', return_value={'models': [entry]}), \
             patch.object(W, '_execute_agent_turn') as execute:
            p = plan(); p['model_id'] = 'codex:test'
            with self.assertRaisesRegex(ValueError, 'orçamento diário'):
                W.execute_agent_turn('codex', 'prompt', 'pedido', '/tmp', [], p)
            execute.assert_not_called()

if __name__ == '__main__':
    unittest.main()


class FlexTests(unittest.TestCase):
    """Flexibilidade: até meio dia acima do teto, só em trabalho complexo/UE5 com o Claude também acima do ritmo."""
    def setUp(self):
        p = patch.object(Q.time, 'time', return_value=NOW)
        p.start(); self.addCleanup(p.stop)
        self.task = dict(chat_id='x', text='Corrige o menu', engine='auto')

    def p(self, used, **extra):
        r = plan(**extra)
        r['quotas']['codex'] = quota(used)
        r['quotas']['claude'] = dict(seven_day_pct=15, pace_target_pct=11)
        return r

    def test_complex_work_just_over_the_ceiling_uses_flex(self):
        self.assertEqual(Q.decide(self.task, self.p(30, dim_level=3), []), ('codex', 'flex'))
        ue = dict(self.task, text='Compila no Unreal via ssh desktop')
        self.assertEqual(Q.decide(ue, self.p(30), []), ('codex', 'flex'))

    def test_simple_work_beyond_margin_or_claude_below_pace_does_not(self):
        self.assertEqual(Q.decide(self.task, self.p(30), [])[1], 'economy')            # simples
        self.assertEqual(Q.decide(self.task, self.p(37, dim_level=3), []), (None, 'daily_budget'))  # > meio dia
        p = self.p(30, dim_level=3)
        p['quotas']['claude']['seven_day_pct'] = 5                                     # Claude tem folga
        self.assertNotEqual(Q.decide(self.task, p, []), ('codex', 'flex'))

    def test_history_is_logged_and_summarised(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / 'flex-history.jsonl'
            q = self.p(30)['quotas']
            Q.log_flex(path, dict(id='t1', chat_id='x', text='UE'), 'codex', q)
            n, worst = Q.flex_summary(path)
            self.assertEqual(n, 1)
            self.assertAlmostEqual(worst, round(30 - 200/7, 2))
