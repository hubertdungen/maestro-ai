import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_image_notes as N


class ImageNotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.old = self.tmp / 'paste-old.png'
        self.new = self.tmp / 'paste-new.png'
        for f in (self.old, self.new):
            f.write_bytes(b'x')
        age = time.time() - 25 * 86400
        os.utime(self.old, (age, age))
        self.chats = {'c1': {'id': 'c1', 'messages': [
            {'role': 'user', 'images': [str(self.old), str(self.new)]}]}}

    def run_pass(self, describe, **kw):
        return N.run_pass(lambda: list(self.chats.values()), self.chats.get,
                          lambda c: self.chats.__setitem__(c['id'], c), describe, **kw)

    def test_only_old_images_without_note_are_described_once(self):
        calls = []
        def describe(p):
            calls.append(p)
            return 'Ecrã do Pulso com 3 alertas'
        self.assertEqual(self.run_pass(describe), 1)
        self.assertEqual(calls, [str(self.old)])          # a imagem nova fica de fora
        self.assertEqual(self.run_pass(describe), 0)      # já tem nota: não repete
        self.assertEqual(len(calls), 1)

    def test_failed_description_is_retried_next_pass(self):
        self.assertEqual(self.run_pass(lambda p: None), 0)
        self.assertNotIn('image_notes', self.chats['c1']['messages'][0])
        self.assertEqual(self.run_pass(lambda p: 'texto da imagem'), 1)

    def test_limit_per_pass(self):
        self.chats['c1']['messages'][0]['images'] = [str(self.old)]
        second = self.tmp / 'paste-old2.png'
        second.write_bytes(b'x'); os.utime(second, (time.time() - 26 * 86400,) * 2)
        self.chats['c1']['messages'][0]['images'].append(str(second))
        self.assertEqual(self.run_pass(lambda p: 'nota longa o suficiente', limit=1), 1)

    def test_history_uses_note_only_when_file_is_gone(self):
        self.run_pass(lambda p: 'Ecrã do Pulso com 3 alertas')
        msg = self.chats['c1']['messages'][0]
        self.assertEqual(N.note_for_history(msg), '')     # ficheiro ainda existe: lê-se a imagem
        self.old.unlink()
        self.assertIn('Ecrã do Pulso com 3 alertas', N.note_for_history(msg))


if __name__ == '__main__':
    unittest.main()
