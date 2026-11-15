import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_web as web


class ChatTitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in [('CHATS_DIR', pathlib.Path(self.tmp.name)), ('TITLE_JOBS', set())]:
            p = patch.object(web, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(web, 'ensure_all_dirs'); p.start(); self.addCleanup(p.stop)
        p = patch.object(web.M, '_extract_deepseek_key', return_value=None); p.start(); self.addCleanup(p.stop)

    def wait(self):
        deadline = time.monotonic() + 3
        while web.TITLE_JOBS and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(web.TITLE_JOBS)

    def create(self):
        chat = web.create_chat()
        web.prepare_chat_title(chat, 'Podes corrigir o hover das baterias do Maestro?')
        web.save_chat(chat)
        return chat

    def test_bounded_budgeted_request_and_validation(self):
        with patch.object(web.M, '_extract_deepseek_key', return_value='fake'), patch.object(
                web.READONLY, '_request', return_value={'choices': [{'message': {'content': 'Animação das baterias'}}]}) as call:
            self.assertEqual(web.generate_chat_title('a'*9000, 'b'*9000), ('Animação das baterias', 'generated'))
            payload = call.call_args.args[3]
            self.assertEqual(payload['max_tokens'], 48)
            self.assertEqual(payload['thinking'], {'type': 'disabled'})
            self.assertNotIn('tools', payload)
            data = json.loads(payload['messages'][1]['content'])
            self.assertEqual(len(data['pedido']), 1400)
            self.assertEqual(len(data['resposta']), 1000)
            call.return_value = {'choices': [{'message': {'content': 'Título\nExplicação'}}]}
            self.assertEqual(web.generate_chat_title('Corrigir hover')[1], 'local')
            call.side_effect = RuntimeError('Budget exhausted')
            self.assertEqual(web.generate_chat_title('Corrigir hover'), ('Corrigir hover', 'local'))

    def test_response_refines_once_and_preserves_messages(self):
        chat = self.create()
        with patch.object(web, 'generate_chat_title', side_effect=[('Hover das baterias', 'generated'), ('Animação suave das baterias', 'generated')]) as gen:
            web.schedule_chat_title(chat['id']); self.wait()
            chat = web.load_chat(chat['id'])
            chat['messages'] = [{'role': 'user', 'text': 'Corrige o hover', 'turn_id': '1'},
                                {'role': 'assistant', 'text': 'Transição corrigida', 'turn_id': '1'}]
            web.save_chat(chat)
            web.schedule_chat_title(chat['id']); self.wait()
            after = web.load_chat(chat['id'])
            self.assertEqual(after['title'], 'Animação suave das baterias')
            self.assertEqual(after['messages'], chat['messages'])
            web.schedule_chat_title(chat['id']); self.wait()
            self.assertEqual(gen.call_count, 2)

    def test_reply_during_generation_and_parallel_deduplication(self):
        chat = self.create()
        entered, release = threading.Event(), threading.Event()
        def generate(request, reply):
            if not reply:
                entered.set(); release.wait(2)
            return ('Resposta' if reply else 'Pedido'), 'generated'
        with patch.object(web, 'generate_chat_title', side_effect=generate) as gen:
            web.schedule_chat_title(chat['id']); self.assertTrue(entered.wait(1))
            chat = web.load_chat(chat['id'])
            chat['messages'] = [{'role': 'user', 'text': 'Primeiro', 'turn_id': '1'},
                                {'role': 'user', 'text': 'Segundo', 'turn_id': '2'},
                                {'role': 'assistant', 'text': 'Outro assunto', 'turn_id': '2'},
                                {'role': 'assistant', 'text': 'Primeira resposta', 'turn_id': '1'}]
            web.save_chat(chat)
            web.schedule_chat_title(chat['id'])
            release.set(); self.wait()
            self.assertEqual(gen.call_count, 2)
            self.assertEqual(gen.call_args.args, ('Primeiro', 'Primeira resposta'))
            self.assertEqual(web.load_chat(chat['id'])['title_stage'], 'response')

    def test_custom_titles_and_deleted_chats_are_preserved(self):
        chat = web.create_chat(title='Nome escolhido')
        web.prepare_chat_title(chat, 'Corrige isto')
        web.save_chat(chat)
        with patch.object(web, 'generate_chat_title') as gen:
            web.schedule_chat_title(chat['id']); self.wait()
            self.assertFalse(gen.called)
        chat = self.create()
        def deleted(*args):
            (web.CHATS_DIR / (chat['id'] + '.json')).unlink()
            return 'Título', 'generated'
        with patch.object(web, 'generate_chat_title', side_effect=deleted):
            web.schedule_chat_title(chat['id']); self.wait()
        self.assertIsNone(web.load_chat(chat['id']))

    def test_attachment_only_request_has_a_name(self):
        chat = web.create_chat()
        web.prepare_chat_title(chat, docs=[{'name': 'Contrato de arrendamento'}])
        self.assertEqual(chat['title'], 'Contrato de arrendamento')
        chat = web.create_chat()
        web.prepare_chat_title(chat)
        self.assertEqual(chat['title'], 'Analisar imagem anexada')


if __name__ == '__main__':
    unittest.main()
