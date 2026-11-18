import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_web as W


class ChatDismissTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for key, value in [('CHATS_DIR', pathlib.Path(temp.name))]:
            p = patch.object(W, key, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(W, 'ensure_all_dirs'); p.start(); self.addCleanup(p.stop)
        self.chat = W.save_chat(dict(id='fixture', project='nas', engine='auto', archived=False,
                                     messages=[dict(role='assistant', text='Sugiro implementar', turn_id='t1')]))

    def test_dismiss_is_saved_without_reordering(self):
        before = self.chat['updated_at']
        out = W.dismiss_chat_items('fixture', ['apply-proposal-t1', 'code-abc'])
        self.assertTrue(out['dismissed']['apply-proposal-t1'])
        saved = W.load_chat('fixture')
        self.assertTrue(saved['dismissed']['code-abc'])
        self.assertEqual(saved['updated_at'], before)

    def test_stale_worker_save_keeps_user_dismissals(self):
        stale = dict(self.chat)
        W.dismiss_chat_items('fixture', ['apply-proposal-t1'])
        W.save_chat(stale)
        self.assertTrue(W.load_chat('fixture')['dismissed']['apply-proposal-t1'])

    def test_empty_or_invalid_keys_are_refused(self):
        with self.assertRaises(ValueError): W.dismiss_chat_items('fixture', [])
        with self.assertRaises(ValueError): W.dismiss_chat_items('fixture', [123])
        with self.assertRaises(KeyError): W.dismiss_chat_items('nao-existe', ['x'])


if __name__ == '__main__':
    unittest.main()
