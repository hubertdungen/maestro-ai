import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import maestro_pulse_weekly as P
import maestro_queue as Q
import maestro_web as W


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=P.TZ).timestamp()


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = stamp('2026-11-28T09:00:00')
        self.state = self.root / 'weekly.json'
        P.save(self.state, dict(enabled=True, enabled_at=self.now - 86400, runs={}))
        self.feed = self.root / 'feed.json'
        P.save(self.feed, dict(generated=datetime.fromtimestamp(self.now, P.TZ).isoformat(),
            items=[dict(id='disk', level='alert', app='Sistema', text='disco cheio')], errors=['test source unavailable']))
        P.save(self.root / 'mail-state.json', dict(open={}))
        (self.root / 'history.jsonl').write_text('')
        self.runner = P.Runner(self.state, self.root, self.feed)

    def test_calendar_and_dst(self):
        self.assertEqual(P.slot(self.now - 1).date().isoformat(), '2026-11-21')
        self.assertEqual(P.slot(self.now).timestamp(), self.now)
        for value in ['2027-03-29T10:00:00', '2027-10-31T10:00:00']:
            due = P.slot(stamp(value))
            self.assertEqual((due.weekday(), due.hour), (5, 9))

    def test_incidents_history_dedupe_and_current_state(self):
        old = dict(id='disk', level='alert', last=self.now - 10, text='old')
        gone = dict(id='gone', alert=True, last=self.now - 100, text='past')
        stale = dict(id='stale', level='alert', last=self.now - 8 * 86400)
        rows = P.candidates(P.read(self.feed), {'disk': old}, [old, gone, stale], self.now)
        self.assertEqual(len(rows), 2)
        self.assertTrue(next(i for i in rows if i['id'] == 'disk')['current'])
        self.assertFalse(next(i for i in rows if i['id'] == 'gone')['current'])

    def test_before_first_saturday_and_stale_feed_do_not_launch(self):
        with patch.object(self.runner, 'api') as api:
            self.runner.tick(self.now - 1)
            api.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, 'desatualizado'):
                self.runner.tick(self.now + 3600)
            api.assert_not_called()

    def test_no_codex_quota_keeps_work_without_paid_fallback(self):
        with patch.object(self.runner, 'api', return_value={'models': [
                dict(id='paid', engine='deepseek', available=True)]}) as api:
            with self.assertRaisesRegex(RuntimeError, 'sem quota'):
                self.runner.tick(self.now)
            self.assertEqual([call.args[0] for call in api.call_args_list], ['/api/models'])
        run = P.read(self.state)['runs']['2026-11-28']
        self.assertNotIn('chat_id', run)
        self.assertIn('prompt', run)

    def test_disabled_repairs_still_expire_owned_chats(self):
        P.save(self.state, dict(enabled=False, enabled_at=self.now, runs={
            'old': dict(chat_id='owned', expires_at=self.now - 1)}))
        with patch.object(self.runner, 'api', return_value={'ok': True}) as api:
            self.runner.tick(self.now)
            self.assertEqual([call.args[0] for call in api.call_args_list],
                ['/api/chat/archive', '/api/chat/delete'])

    def completed_run(self, actions):
        run = dict(chat_id='owned', submitted_at=self.now - 60, expires_at=self.now + 100)
        snapshot = dict(running=False, queue=[], chat=dict(messages=[dict(role='assistant',
            text='Resultado\n```pulse-action\n' + json.dumps(dict(actions=actions)) + '\n```')]))
        return run, snapshot

    def test_action_email_only_for_explicit_human_action_once(self):
        run, snapshot = self.completed_run([dict(problem='Login expirado',
            reason='Só o titular pode autenticar', action='Abre o serviço e inicia sessão')])
        with patch.object(self.runner, 'api', return_value=snapshot), patch.object(self.runner, 'send_action_email') as send:
            self.assertTrue(self.runner.notify_action('2026-11-28', run, self.now))
            self.runner.notify_action('2026-11-28', run, self.now + 60)
            send.assert_called_once()
            self.assertIn('Abre o serviço', send.call_args.args[1])
            self.assertIn('Só o titular', send.call_args.args[1])
        self.assertEqual(run['action_email_sent_at'], self.now)

    def test_no_email_for_resolved_or_running_or_queued(self):
        run, snapshot = self.completed_run([])
        with patch.object(self.runner, 'api', return_value=snapshot), patch.object(self.runner, 'send_action_email') as send:
            snapshot['running'] = True
            self.assertFalse(self.runner.notify_action('week', run, self.now))
            snapshot.update(running=False, queue=[dict(status='queued')])
            self.assertFalse(self.runner.notify_action('week', run, self.now))
            snapshot['queue'] = []
            self.assertTrue(self.runner.notify_action('week', run, self.now))
            send.assert_not_called()

    def test_email_failure_retries_before_expiring_chat(self):
        run, snapshot = self.completed_run([dict(problem='p', reason='r', action='a')])
        run['expires_at'] = self.now - 1
        P.save(self.state, dict(enabled=False, runs={'week': run}))
        with patch.object(self.runner, 'api', return_value=snapshot) as api, patch.object(
                self.runner, 'send_action_email', side_effect=[OSError('SMTP offline'), None]) as send:
            with self.assertRaisesRegex(OSError, 'SMTP offline'):
                self.runner.tick(self.now)
            self.assertNotIn('action_checked_at', P.read(self.state)['runs']['week'])
            self.assertEqual([c.args[0] for c in api.call_args_list], ['/api/chat/job?id=owned'])
            state = self.runner.tick(self.now + 60)
            self.assertEqual(send.call_count, 2)
            self.assertTrue(state['runs']['week']['deleted_at'])
            self.runner.tick(self.now + 120)
            self.assertEqual(send.call_count, 2)

    def test_malformed_action_report_never_sends_or_expires(self):
        for actions in [None, [dict(problem='p', reason='r', action='')]]:
            run, snapshot = self.completed_run(actions)
            with patch.object(self.runner, 'api', return_value=snapshot), patch.object(self.runner, 'send_action_email') as send:
                with self.assertRaisesRegex(RuntimeError, 'inválido'):
                    self.runner.notify_action('week', run, self.now)
                send.assert_not_called()
        snapshot['chat']['messages'][-1]['text'] = 'Bloqueado por quota, aguardar reset'
        with patch.object(self.runner, 'api', return_value=snapshot), patch.object(self.runner, 'send_action_email') as send:
            with self.assertRaisesRegex(RuntimeError, 'estruturado'):
                self.runner.notify_action('week', run, self.now)
            send.assert_not_called()

    def test_pulso_transport_sends_escaped_multipart_to_configured_recipient(self):
        (self.root / 'pulse.py').write_text('')
        source = (P.PULSE / 'notify.py').read_text()
        (self.root / 'notify.py').write_text(source)
        # Exercise the real Pulso transport with fixture credentials and mocked SMTP.
        conf = self.root / 'smtp.json'
        P.save(conf, dict(host='smtp.example.test', user='fixture', password='fixture',
            to='owner@example.test', **{'from': 'pulso@example.test'}))
        (self.root / 'notify.py').write_text(source.replace(
            'CONF = Path.home() / ".config/pulso-mail.json"', 'CONF = Path(' + repr(str(conf)) + ')'))
        with patch('smtplib.SMTP') as smtp:
            self.runner.send_action_email('Ação necessária', 'Abre <serviço> & inicia sessão')
            transport = smtp.return_value.__enter__.return_value
            transport.starttls.assert_called_once()
            transport.login.assert_called_once_with('fixture', 'fixture')
            msg = transport.send_message.call_args.args[0]
            self.assertEqual(msg['To'], 'owner@example.test')
            self.assertIn('Abre <serviço>', msg.get_body(preferencelist=('plain',)).get_content())
            self.assertIn('&lt;serviço&gt; &amp;', msg.get_body(preferencelist=('html',)).get_content())

    def test_real_http_queue_retry_retention_and_no_live_state(self):
        queue = Q.TurnQueue(self.root / 'queue.json')
        patches = [patch.object(W, 'CHATS_DIR', self.root / 'chats'),
            patch.object(W, 'ensure_all_dirs', lambda: (self.root / 'chats').mkdir(exist_ok=True)),
            patch.object(W, 'TURN_QUEUE', queue), patch.object(W, 'ACTIVE_JOBS', {}),
            patch.object(W, 'schedule_chat_title'),
            patch.object(W.MODELS, 'resolve', return_value={'engine': 'codex', 'effort': None}),
            patch.object(W.MODELS, 'catalog', return_value={'models': [dict(id='codex:fixture', engine='codex', available=True)]}),
            patch.object(W.M, 'get_claude_quota', return_value={}),
            patch.object(W.M, 'get_codex_quota', return_value={}),
            patch.object(W.M, 'get_antigravity_status', return_value={}),
            patch.object(W.M, 'get_deepseek_balance', return_value={})]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        server = ThreadingHTTPServer(('127.0.0.1', 0), W.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.runner.base = f'http://127.0.0.1:{server.server_port}'
        unrelated = W.create_chat('nas', 'codex', 'existing user chat')
        # Simulate lost reply AFTER the real server accepts the queue turn.
        real_api = self.runner.api
        def lost_reply(route, body=None):
            result = real_api(route, body)
            if route == '/api/chat/send':
                raise TimeoutError('reply lost')
            return result
        with patch.object(self.runner, 'api', side_effect=lost_reply):
            with self.assertRaises(TimeoutError): self.runner.tick(self.now)
        state = self.runner.tick(self.now + 60)
        run = state['runs']['2026-11-28']
        self.assertEqual(len(queue.tasks), 1)
        self.assertEqual(queue.tasks[0]['model_id'], 'codex:fixture')
        self.assertIn('test source unavailable', queue.tasks[0]['text'])
        self.runner.tick(self.now + 120)
        self.assertEqual(len(queue.tasks), 1)
        self.assertEqual(len(W.list_chats()), 2)
        # A running repair survives expiry, and is deleted on the next idle tick.
        W.ACTIVE_JOBS['busy'] = dict(chat_id=run['chat_id'], status='running')
        self.runner.tick(run['expires_at'])
        self.assertIsNotNone(W.load_chat(run['chat_id']))
        W.ACTIVE_JOBS.clear()
        queue.update(queue.tasks[0]['id'], status='done')
        chat = W.load_chat(run['chat_id'])
        chat['messages'].append(dict(role='assistant', text='Saudável\n```pulse-action\n{"actions": []}\n```'))
        W.save_chat(chat)
        self.runner.tick(run['expires_at'] + 60)
        self.assertIsNone(W.load_chat(run['chat_id']))
        self.assertIsNotNone(W.load_chat(unrelated['id']))
        self.assertEqual(W.list_chats()[0]['id'], unrelated['id'])
        self.assertTrue(P.read(self.state)['runs']['2026-11-28']['deleted_at'])


if __name__ == '__main__':
    unittest.main()
