import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import maestro_pulse_weekly as P
import maestro_pulse_report as R


class ReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'state.json'
        self.runner = P.Runner(self.path, self.root)

    def completed(self, actions=None):
        run = dict(chat_id='fixture', submitted_at=100, created_at=99, expires_at=200)
        snapshot = dict(chat=dict(messages=[dict(role='assistant', text=
            'Corrigido <disco> & validado: 20 GB livres.\n```pulse-action\n' +
            json.dumps(dict(actions=actions or [])) + '\n```')]))
        return run, snapshot

    def test_summary_saved_without_email_then_survives_chat_deletion(self):
        run, snapshot = self.completed()
        with patch.object(self.runner, 'api', return_value=snapshot), patch.object(self.runner, 'send_action_email') as send:
            self.runner.notify_action('week', run, 150)
            send.assert_not_called()
        run['deleted_at'] = 200
        P.save(self.path, dict(runs={'week': run}))
        markup, plain = R.render(90, 300, self.path)
        self.assertIn('20 GB livres', plain)
        self.assertIn('&lt;disco&gt; &amp;', markup)
        self.assertNotIn('pulse-action', plain)
        self.assertEqual(run['completed_at'], 150)

    def test_late_completion_emails_summary_once(self):
        run, snapshot = self.completed()
        P.save(self.root / 'digest-state.json', dict(last_digest=dict(at=120)))
        with patch.object(self.runner, 'api', return_value=snapshot), patch.object(self.runner, 'send_action_email') as send:
            self.runner.notify_action('week', run, 150)
            self.runner.notify_action('week', run, 160)
            send.assert_called_once()
            self.assertIn('20 GB livres', send.call_args.args[1])
        self.assertEqual(run['completion_email_sent_at'], 150)

    def test_action_email_includes_summary_without_duplicate_completion(self):
        run, snapshot = self.completed([dict(problem='p', reason='r', action='a')])
        P.save(self.root / 'digest-state.json', dict(last_digest=dict(at=120)))
        with patch.object(self.runner, 'api', return_value=snapshot), patch.object(self.runner, 'send_action_email') as send:
            self.runner.notify_action('week', run, 150)
            send.assert_called_once()
            self.assertIn('20 GB livres', send.call_args.args[1])
            self.assertIn('Ação necessária: a', send.call_args.args[1])

    def test_pending_missing_and_out_of_period_do_not_claim_success(self):
        P.save(self.path, dict(runs={'week': dict(created_at=100)}))
        self.assertIn('aguarda execução', R.render(90, 150, self.path)[1])
        self.assertIn('Sem revisão', R.render(110, 150, self.path)[1])
        self.path.write_text('invalid')
        self.assertIn('Não foi possível', R.render(90, 150, self.path)[1])

    def test_install_idempotent_and_hook_renders_both_digest_kinds(self):
        path = self.root / 'notify.py'
        path.write_text('def digest():\n    body, text = [], []\n    eps = episodes(start.timestamp())\n')
        R.install(path)
        installed = path.read_text()
        R.install(path)
        self.assertEqual(installed, path.read_text())
        self.assertEqual(installed.count(R.MARKER), 1)
        self.assertIn('repair_text', installed)
        path.write_text('unexpected source')
        with self.assertRaises(RuntimeError):
            R.install(path)
        self.assertEqual(path.read_text(), 'unexpected source')


if __name__ == '__main__':
    unittest.main()
