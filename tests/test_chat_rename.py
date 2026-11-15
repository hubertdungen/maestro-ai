import copy
import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_web as W


class ChatRenameTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, value in [('CHATS_DIR', pathlib.Path(tmp.name)), ('TITLE_JOBS', set())]:
            p = patch.object(W, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(W, 'ensure_all_dirs'); p.start(); self.addCleanup(p.stop)
        self.chat = W.save_chat(dict(id='rename-fixture', title='Original', title_source='provisional',
                                    messages=[dict(role='user', text='Primeiro pedido')], archived=True,
                                    folder_id='web', updated_at=123))

    def test_rename_normalizes_preserves_history_and_allows_repeated_edits(self):
        result = W.rename_chat(self.chat['id'], '  Nome novo   com espaços  ')
        self.assertEqual(result['title'], 'Nome novo com espaços')
        self.assertEqual(result['title_source'], 'manual')
        for key in ('messages', 'folder_id', 'archived', 'updated_at'):
            self.assertEqual(result[key], self.chat[key])
        self.assertEqual(W.rename_chat(self.chat['id'], 'Segundo nome')['title'], 'Segundo nome')

    def test_worker_snapshot_cannot_overwrite_manual_name(self):
        stale = copy.deepcopy(self.chat)
        W.rename_chat(stale['id'], 'O meu nome')
        stale['messages'].append(dict(role='assistant', text='Resposta'))
        W.save_chat(stale)
        result = W.load_chat(stale['id'])
        self.assertEqual(result['title'], 'O meu nome')
        self.assertEqual(len(result['messages']), 2)

    def test_inflight_auto_title_cannot_overwrite_same_manual_name(self):
        def generate(*args):
            W.rename_chat(self.chat['id'], self.chat['title'])
            return 'Nome automático', 'generated'
        with patch.object(W, 'generate_chat_title', side_effect=generate):
            W.schedule_chat_title(self.chat['id'])
            import time
            deadline = time.monotonic() + 3
            while W.TITLE_JOBS and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertFalse(W.TITLE_JOBS)
        self.assertEqual(W.load_chat(self.chat['id'])['title_source'], 'manual')
        self.assertEqual(W.load_chat(self.chat['id'])['title'], 'Original')

    def test_validation_and_http_errors(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), W.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def post(data):
            request = urllib.request.Request('http://127.0.0.1:%d/api/chat/rename' % server.server_port,
                data=json.dumps(data).encode(), headers={'Content-Type':'application/json', 'X-Maestro-Action':'1'})
            try:
                with urllib.request.urlopen(request) as response: return response.status, json.load(response)
            except urllib.error.HTTPError as e: return e.code, json.load(e)
        for title in ('', '  ', 'x'*121, None, 123):
            self.assertEqual(post(dict(id=self.chat['id'], title=title))[0], 400)
        self.assertEqual(post(dict(id='missing',title='Nome'))[0], 404)
        status, data = post(dict(id=self.chat['id'],title='Nome válido'))
        self.assertEqual(status, 200); self.assertEqual(data['chat']['title'], 'Nome válido')


if __name__ == '__main__': unittest.main()
