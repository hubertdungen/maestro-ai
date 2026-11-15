import time
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_models as M
import maestro_model_policy as P
import maestro_web as W
import maestro as CORE


class RoutingTests(unittest.TestCase):
    def setUp(self):
        telemetry = patch.object(W.EFFICIENCY, 'record_call')
        telemetry.start()
        self.addCleanup(telemetry.stop)
        self.entries = [M._entry('codex', model, model, ['low', 'medium', 'high'], 'low', 'test')
                        for model in ('gpt-6.1-sol', 'gpt-6-luna', 'gpt-6.1-terra', 'gpt-6-astra')]
        self.entries += [M._entry('sonnet', 'sonnet', 'Sonnet', ['low', 'high'], 'low', 'test'),
                         M._entry('deepseek', 'configured', 'DeepSeek', [], None, 'test')]
        for e in self.entries:
            e.update(read_only_enforced=e['engine'] != 'deepseek')

    def test_economy_general_and_complex_routing(self):
        self.assertEqual(P.ranked(self.entries, 'scout', engine='codex')[0]['model'], 'gpt-6-luna')
        self.assertEqual(P.ranked(self.entries, 'implementation', engine='codex')[0]['model'], 'gpt-6.1-sol')
        self.assertEqual(P.profile(P.ranked(self.entries, 'complex', engine='codex')[0])['tier'], 'advanced')
        self.assertEqual(P.effort_for(self.entries[0], 'scout'), 'low')
        self.assertEqual(P.effort_for(self.entries[0], 'complex'), 'high')

    def test_reading_capability_does_not_mean_reading_only(self):
        self.assertTrue(self.entries[0]['read_only_enforced'])
        self.assertNotEqual(self.entries[0].get('transport'), 'deepseek-api')

    def test_rejections_never_replay_partial_work_or_generic_errors(self):
        self.assertEqual(P.rejection_reason(1, '', 'You have hit your usage limit'), 'quota')
        self.assertEqual(P.rejection_reason(1, 'Error: model x not found'), 'model')
        self.assertIsNone(P.rejection_reason(1, 'Changed config', 'usage limit'))
        self.assertIsNone(P.rejection_reason(1, '', 'exec\napply_patch ran\nusage limit'))
        self.assertIsNone(P.rejection_reason(1, 'Build failed'))
        self.assertIsNone(P.rejection_reason(0, 'usage limit'))

    def call(self, results, **extra):
        plan = dict(quotas={'codex': {'seven_day_pct': 20, 'seven_day_reset': time.time()+3*86400}}, exec_engine='codex', domain='NAS', dim_level=2, **extra)
        with patch.object(M, 'catalog', return_value={'models': self.entries}), \
             patch.object(W, '_execute_agent_turn', side_effect=results) as execute:
            result = W._execute_with_reserve('codex', 'Objective', 'Task', '/tmp', [], plan, 'nas', None)
            return result, execute.call_args_list

    def test_quota_skips_all_models_sharing_account(self):
        r, calls = self.call([dict(rc=1, provider_rejection='quota'), dict(rc=0, text='Done')])
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[1].args[0], 'codex')
        self.assertEqual(len(r['attempts']), 2)
        self.assertEqual(len(r['efficiency_calls']), 2)

    def test_model_rejection_can_try_another_id_with_same_account(self):
        r, calls = self.call([dict(rc=1, provider_rejection='model'), dict(rc=0, text='Done')])
        self.assertEqual(calls[1].args[0], 'codex')
        self.assertNotEqual(r['attempts'][0]['model_id'], r['attempts'][1]['model_id'])

    def test_explicit_model_is_never_substituted(self):
        r, calls = self.call([dict(rc=1, provider_rejection='quota')], model_id='codex:gpt-6.1-sol')
        self.assertEqual(len(calls), 1)

    def test_partial_failure_does_not_try_reserves(self):
        r, calls = self.call([dict(rc=1, text='Changed a file, build failed')])
        self.assertEqual(len(calls), 1)

    def test_reserve_has_same_readonly_and_vision_restrictions(self):
        self.entries[4]['available'] = False
        with patch.object(M, 'catalog', return_value={'models': self.entries}), \
             patch.object(W, '_execute_agent_turn', return_value=dict(rc=1, provider_rejection='quota')) as execute:
            W._execute_with_reserve('codex', 'Task', 'Task', '/tmp', ['image.png'],
                                   dict(quotas={'codex': {'seven_day_pct': 20, 'seven_day_reset': time.time()+3*86400}}, exec_engine='codex', read_only=True), 'nas', None)
        self.assertEqual(execute.call_count, 1)

    def test_specialist_route_is_independent_of_maestro_hierarchy(self):
        claude = dict(pace_status='ok', five_hour_pct=20, seven_day_pct=10)
        with patch.object(CORE, 'get_claude_quota', return_value=claude), \
             patch.object(CORE, 'get_codex_quota', return_value=dict(five_hour_est_pct=20)), \
             patch.object(CORE, 'get_deepseek_balance', return_value=dict(available=True)), \
             patch.object(CORE, 'get_antigravity_status', return_value=dict(limited=False)), \
             patch.object(CORE, 'choose_active_maestro', return_value=dict(engine='agy', name='Antigravity')):
            web = CORE.classify_task('corrigir bug no layout HTML', '/tmp')
            self.assertEqual(web['exec_engine'], 'claude-sonnet')
            self.assertEqual(web['maestro_engine'], 'agy')
            nas = CORE.classify_task('corrigir erro no Docker', '/tmp')
            self.assertEqual(nas['exec_engine'], 'codex')
            unity = CORE.classify_task('arquitetura complexa Unity', '/tmp')
            self.assertEqual(unity['exec_engine'], 'claude-opus')

    def test_opus_automatic_routing_protects_pace_budget(self):
        entries = dict(models=[M._entry('opus', 'opus', 'Opus', [], None, 'test')])
        with patch.object(M, '_CACHE', entries), patch.object(M, '_CACHE_AT', M.time.time()):
            catalogue = M.catalog(quotas={'claude': {'five_hour_pct': 67, 'pace_status': 'ok'}})
        self.assertTrue(catalogue['models'][0]['available'])
        self.assertFalse(P.ranked(catalogue['models'], 'complex'))


if __name__ == '__main__':
    unittest.main()
