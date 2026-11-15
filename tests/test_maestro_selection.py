"""Principal selection must distinguish confirmed limits from missing telemetry."""
import pathlib
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro as M
import maestro_web as W


class SelectionTests(unittest.TestCase):
    def choose(self, codex=None, agy=True, claude=True, force=False):
        q = dict(five_hour_est_pct=46, source='wham_live', pace_blocked=False)
        q.update(codex or {})
        with patch.object(M, 'get_codex_quota', return_value=q), \
             patch.object(M, 'get_antigravity_status', return_value=dict(installed=agy, limited=not agy)), \
             patch.object(M, 'get_claude_quota', return_value=dict(pace_status='ok' if claude else 'limitado', five_hour_pct=10)):
            return M.choose_active_maestro(force)

    def test_codex_precedes_available_antigravity(self):
        self.assertEqual(self.choose()['engine'], 'codex')
        self.assertFalse(self.choose()['is_substitute'])

    def test_historical_estimate_does_not_confirm_pace_block(self):
        self.assertEqual(self.choose(dict(source='history_est', pace_blocked=True))['engine'], 'codex')
        self.assertEqual(self.choose(dict(pace_status='desconhecido', pace_blocked=True))['engine'], 'codex')

    def test_confirmed_limits_and_forced_substitute_skip_principal(self):
        for q in [dict(pace_blocked=True), dict(limit_reached=True), dict(five_hour_est_pct=85)]:
            self.assertEqual(self.choose(q)['engine'], 'agy')
        self.assertEqual(self.choose(force=True)['engine'], 'agy')

    def test_fallback_order(self):
        self.assertEqual(self.choose(agy=False, force=True)['engine'], 'claude-sonnet')
        self.assertEqual(self.choose(agy=False, claude=False, force=True)['engine'], 'deepseek')

    def test_sidebar_live_snapshot_replaces_saved_snapshot(self):
        def flow(status):
            return dict(nodes=[dict(id='scout',title='Scout',status=status,engine='codex')])
        chat = dict(id='chat',messages=[dict(turn_id='t',plan=dict(workflow=flow('pending')))])
        jobs = dict(t=dict(chat_id='chat',task_id='t',status='running',workflow=flow('running')))
        with patch.object(W, 'ACTIVE_JOBS', jobs):
            result = W.chat_subtasks(chat)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['status'], 'running')
        self.assertEqual(chat['messages'][0]['plan']['workflow']['nodes'][0]['status'], 'pending')

if __name__ == '__main__':
    unittest.main()
