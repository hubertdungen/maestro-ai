import json
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_budget as B
import maestro_deepseek_gateway as G


class GatewayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name)/'budget.json'
        self.server = G.gateway('provider-secret', 'local-token', self.path)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def test_authentication_routes_and_server_tools_cannot_reach_provider(self):
        with patch.object(G.requests, 'post') as upstream:
            # requests.Session bypasses our patched module-level upstream method.
            with requests.Session() as client:
                r = client.post(self.url+'/v1/messages', json={})
                self.assertEqual(r.status_code, 403)
                r = client.post(self.url+'/v1/files', headers={'x-api-key':'local-token'}, json={})
                self.assertEqual(r.status_code, 403)
                r = client.post(self.url+'/v1/messages', headers={'x-api-key':'local-token'}, json=dict(
                    model='deepseek-flash', tools=[dict(type='web_search_20250305')]))
                self.assertEqual(r.status_code, 429)
            upstream.assert_not_called()

    def test_stream_is_relayed_and_settled_with_usage(self):
        body = (b'data: {"type":"message_start","message":{"usage":{"input_tokens":10,"cache_read_input_tokens":5}}}\n\n'
                b'data: {"type":"message_delta","usage":{"output_tokens":20}}\n\n')
        class Response:
            status_code = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_content(self, size): return iter([body])
        with patch.object(G.requests, 'post', return_value=Response()) as upstream:
            with requests.Session() as client:
                r = client.post(self.url+'/v1/messages', headers={'x-api-key':'local-token'}, json=dict(
                    model='deepseek-flash', messages=[dict(role='user',content='Hi')], max_tokens=100, stream=True))
            self.assertEqual((r.status_code, r.content), (200, body))
            self.assertEqual(upstream.call_args.kwargs['headers']['x-api-key'], 'provider-secret')
        self.assertAlmostEqual(B.status(self.path)['today_eur'], 1.1*(15*.3+20*1.2)/1e6, places=6)

    def test_insufficient_budget_blocks_upstream(self):
        with B.ledger(self.path) as d:
            d['days'][G.B.dt.datetime.now(B.TZ).date().isoformat()] = 15
        with patch.object(G.requests, 'post') as upstream:
            with requests.Session() as client:
                r = client.post(self.url+'/v1/messages', headers={'x-api-key':'local-token'}, json=dict(
                    model='deepseek-flash', max_tokens=100, messages=[]))
            self.assertEqual(r.status_code, 429)
            upstream.assert_not_called()
