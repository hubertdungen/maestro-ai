import copy
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_web as W
import maestro_queue as Q


class ChatArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = pathlib.Path(temp.name)
        self.queue = Q.TurnQueue(root / 'queue.json')
        for key, value in [('CHATS_DIR', root), ('TURN_QUEUE', self.queue), ('ACTIVE_JOBS', {})]:
            p = patch.object(W, key, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(W, 'ensure_all_dirs'); p.start(); self.addCleanup(p.stop)
        self.chat = W.save_chat(dict(id='fixture', project='nas', folder_id='web', engine='auto',
                                    messages=[dict(role='user', text='Um pedido', images=['/image.png']),
                                              dict(role='assistant', text='Resposta', plan={'exec_engine':'codex'})]))

    def test_roundtrip_keeps_history_attachments_filing_and_order(self):
        before = copy.deepcopy(self.chat)
        W.set_chat_archived('fixture', True)
        self.assertEqual(W.list_chats(), [])
        self.assertEqual(W.list_chats(True)[0]['engines'], ['codex'])
        archived = W.load_chat('fixture')
        for key, value in before.items():
            self.assertEqual(archived[key], value)
        self.assertEqual(W.chat_activity(archived)['state'], 'archived')
        W.set_chat_archived('fixture', False)
        self.assertEqual(W.list_chats(True), [])
        self.assertEqual(len(W.list_chats()), 1)
        self.assertEqual(W.load_chat('fixture')['messages'], before['messages'])

    def test_stale_save_cannot_undo_archive_or_restore(self):
        stale = copy.deepcopy(self.chat)
        W.set_chat_archived('fixture', True)
        W.save_chat(stale)
        self.assertTrue(W.load_chat('fixture')['archived'])
        stale = W.load_chat('fixture')
        W.set_chat_archived('fixture', False)
        W.save_chat(stale)
        self.assertFalse(W.load_chat('fixture')['archived'])

    def test_active_worker_and_admitted_task_cannot_be_archived(self):
        W.ACTIVE_JOBS['worker'] = dict(chat_id='fixture', status='running')
        with self.assertRaises(ValueError): W.set_chat_archived('fixture', True)
        W.ACTIVE_JOBS.clear()
        task, _ = self.queue.add(dict(id='fixture', text='pedido'))
        self.queue.update(task['id'], status='running')
        with self.assertRaises(ValueError): W.set_chat_archived('fixture', True)
        self.assertFalse(W.load_chat('fixture').get('archived', False))

    def test_archived_queue_spends_no_planning_or_execution_and_is_recoverable(self):
        task, _ = self.queue.add(dict(id='fixture', text='pedido', project='nas'))
        W.set_chat_archived('fixture', True)
        with patch.object(W.M, 'classify_task') as classify, patch.object(W, 'dispatch_turn') as dispatch:
            W.schedule_turns_once()
            classify.assert_not_called(); dispatch.assert_not_called()
        self.assertEqual(self.queue.pending('fixture')[0]['id'], task['id'])
        self.assertIsNone(W.chat_activity_snapshot('fixture')['job'])
        # Reload disk state, as after a service restart.
        self.assertEqual(Q.TurnQueue(self.queue.path).pending('fixture')[0]['status'], 'queued')
        W.set_chat_archived('fixture', False)
        with patch.object(W, 'get_folder_map', return_value={}), patch.object(W.M, 'classify_task', return_value={}), \
             patch.object(W.Q, 'decide', return_value=(None, 'quota')) as decide:
            W.schedule_turns_once()
            decide.assert_called_once()

    def test_archived_chat_cannot_accept_send_or_steer(self):
        W.set_chat_archived('fixture', True)
        for send in [W.enqueue_turn, W.dispatch_turn]:
            with self.assertRaises(ValueError): send(dict(id='fixture', text='novo', steer=True))
        self.assertEqual(self.queue.tasks, [])

    def test_multiple_engine_families_and_unknown_chat(self):
        self.chat['messages'] += [dict(role='assistant', plan={'exec_engine':'opus'}),
                                 dict(role='assistant', plan={'exec_engine':'agy'})]
        W.save_chat(self.chat)
        W.set_chat_archived('fixture', True)
        self.assertEqual(W.list_chats(True)[0]['engines'], ['agy', 'claude', 'codex'])
        with self.assertRaises(KeyError): W.set_chat_archived('missing', True)

    def test_scheduler_rechecks_archive_after_planning(self):
        task, _ = self.queue.add(dict(id='fixture', text='pedido', project='nas'))
        def decide(*args):
            W.set_chat_archived('fixture', True)
            return 'codex', 'ready'
        with patch.object(W, 'get_folder_map', return_value={}), patch.object(W.M, 'classify_task', return_value={}), \
             patch.object(W.Q, 'decide', side_effect=decide), patch.object(W, 'dispatch_turn') as dispatch:
            W.schedule_turns_once()
            dispatch.assert_not_called()
        self.assertEqual(self.queue.pending('fixture')[0]['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
