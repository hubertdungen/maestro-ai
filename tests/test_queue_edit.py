"""Queue changes must survive reloads and never mutate admitted work."""
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_queue as Q
import maestro_web as W
import test_turn_queue as fixtures
from test_turn_queue import plan


class PendingChanges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.queue = Q.TurnQueue(pathlib.Path(self.tmp.name) / 'queue.json')
        self.a, _ = self.queue.add(dict(id='chat', text='A', images=['image.png'], docs=[{'name':'doc'}]))
        self.other, _ = self.queue.add(dict(id='other', text='Other'))
        self.b, _ = self.queue.add(dict(id='chat', text='B'))

    def test_reorder_persists_and_preserves_other_chats(self):
        self.queue.reorder_pending('chat', [self.b['id'], self.a['id']])
        restored = Q.TurnQueue(self.queue.path)
        self.assertEqual([t['text'] for t in restored.tasks], ['B', 'Other', 'A'])
        self.assertEqual(restored.tasks[-1]['images'], ['image.png'])

    def test_stale_or_foreign_order_cannot_drop_or_duplicate_work(self):
        for ids in ([self.a['id']], [self.a['id']]*2, [self.a['id'], self.other['id']], None, [{}]):
            with self.assertRaises(ValueError): self.queue.reorder_pending('chat', ids)
        self.queue.update(self.b['id'], status='running')
        with self.assertRaises(ValueError): self.queue.reorder_pending('chat', [self.b['id'], self.a['id']])
        self.assertEqual(self.queue.tasks[-1]['status'], 'running')

    def test_edit_preserves_attachments_and_resets_admission(self):
        self.queue.update(self.a['id'], force=True, checked_at=123, conflict='red')
        self.queue.edit_pending('chat', self.a['id'], 'Changed', 'A')
        task = Q.TurnQueue(self.queue.path).tasks[0]
        self.assertEqual((task['text'],task['images'],task['docs']), ('Changed',['image.png'],[{'name':'doc'}]))
        self.assertFalse(task['force']); self.assertEqual(task['checked_at'], 0)
        for cid, expected in [('other','Changed'), ('chat','A')]:
            with self.assertRaises(ValueError): self.queue.edit_pending(cid, self.a['id'], 'Oops', expected)
        self.queue.update(self.a['id'], status='running')
        with self.assertRaises(ValueError): self.queue.edit_pending('chat', self.a['id'], 'Oops', 'Changed')
        with self.assertRaises(ValueError): self.queue.edit_pending('chat', self.b['id'], ' ', 'B')


class SchedulingChanges(unittest.TestCase):
    setUp = fixtures.IntegrationTests.setUp
    enqueue = fixtures.IntegrationTests.enqueue
    def test_reorder_controls_actual_dispatch(self):
        self.enqueue('A'); self.enqueue('B')
        self.queue.reorder_pending('chat', [t['id'] for t in reversed(self.queue.tasks)])
        with patch.object(W, 'dispatch_turn') as dispatch:
            W.schedule_turns_once()
        self.assertEqual(dispatch.call_args_list[0].args[0]['text'], 'B')

    def test_edit_during_classification_invalidates_old_launch(self):
        self.enqueue('A')
        def classify(*args):
            t = self.queue.tasks[0]
            self.queue.edit_pending('chat', t['id'], 'Changed', 'A')
            return plan()
        with patch.object(W.M, 'classify_task', side_effect=classify), patch.object(W, 'dispatch_turn') as dispatch:
            W.schedule_turns_once()
            dispatch.assert_not_called()
        self.assertEqual(self.queue.tasks[0]['text'], 'Changed')
        self.assertEqual(self.queue.tasks[0]['status'], 'queued')

    def test_reordered_interrupted_predecessor_uses_visible_order(self):
        self.enqueue('A'); self.enqueue('B')
        self.queue.update(self.queue.tasks[0]['id'], status='interrupted')
        self.queue.reorder_pending('chat', [t['id'] for t in reversed(self.queue.tasks)])
        with patch.object(W, 'dispatch_turn') as dispatch:
            W.schedule_turns_once()
        self.assertEqual(dispatch.call_args.args[0]['text'], 'B')
