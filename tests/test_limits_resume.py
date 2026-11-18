import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_queue as Q


class LimitsResumeTests(unittest.TestCase):
    def test_first_look_only_records_baseline(self):
        self.assertEqual(Q.resumable({}, ['codex']), (False, ['codex']))

    def test_resumes_when_a_plan_gains_quota(self):
        task = {'limits_sig': ['codex']}
        self.assertTrue(Q.resumable(task, ['codex', 'claude-sonnet'])[0])   # reset do Claude
        self.assertFalse(Q.resumable(task, ['codex'])[0])                   # nada de novo
        self.assertFalse(Q.resumable({'limits_sig': ['codex'], 'auto_resumes': Q.MAX_AUTO_RESUMES},
                                     ['claude-sonnet'])[0])                 # teto

    def test_usable_filters_by_cached_quota(self):
        plan = {'available_engines': ['codex', 'claude-sonnet', 'deepseek'],
                'quotas': {'codex': {'five_hour_est_pct': 10, 'seven_day_pct': 100},
                           'claude': {'five_hour_pct': 5, 'seven_day_pct': 50, 'pace_status': 'ok'},
                           'deepseek': {'available': True}}}
        self.assertEqual(Q.usable(plan), ['claude-sonnet', 'deepseek'])


if __name__ == '__main__':
    unittest.main()
