"""Run dsh through a loopback billing gateway, reserving cost before each call.

The provider key stays in the gateway. The child gets a random local credential.
Model discovery, Messages and metered Search/Fetch requests are exposed. Uploads
are excluded. Streaming is relayed with usage settled on completion.
"""
import json
import os
import pathlib
import secrets
import subprocess
import sys
import threading
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
import maestro_budget as B


def cli_command(argv):
    return [sys.executable, str(pathlib.Path(__file__).resolve()), '--'] + list(argv)


def stream_usage(body):
    usage = {}
    for line in body.splitlines():
        if not line.startswith(b'data: '):
            continue
        try:
            event = json.loads(line[6:])
        except ValueError:
            continue
        usage.update((event.get('message') or {}).get('usage') or {})
        usage.update(event.get('usage') or {})
    # Messages input_tokens excludes cached tokens; conservative peak/miss billing.
    prompt = usage.get('input_tokens')
    output = usage.get('output_tokens')
    if isinstance(prompt, int):
        prompt += usage.get('cache_read_input_tokens', 0) + usage.get('cache_creation_input_tokens', 0)
    return dict(prompt_tokens=prompt, completion_tokens=output)


def gateway(key, credential, budget_path=None, necessity='normal', reason=''):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, content_type='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            token = self.headers.get('x-api-key') or self.headers.get('Authorization', '').removeprefix('Bearer ')
            return secrets.compare_digest(token, credential)

        def do_GET(self):
            if not self.authorized() or self.path != '/models':
                return self.reply(403, b'{}')
            # Local, verified model catalogue; discovery incurs no API cost.
            models = [dict(id=m, object='model') for m in ('deepseek-flash', 'deepseek-v4-pro')]
            self.reply(200, json.dumps(dict(object='list', data=models)).encode())

        def do_POST(self):
            if self.authorized() and self.path in ('/v1/tools/search', '/v1/tools/fetch'):
                return self.web_request()
            if not self.authorized() or self.path not in ('/anthropic/v1/messages', '/v1/messages'):
                return self.reply(403, b'{}')
            begun = False
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4_000_000:
                    raise ValueError('Pedido demasiado grande')
                payload = json.loads(self.rfile.read(size))
                # Server-side search/fetch billing must never bypass token admission.
                if any(t.get('type', 'custom') != 'custom' for t in payload.get('tools', [])):
                    raise ValueError('Ferramentas pagas de servidor indisponíveis neste adaptador')
                payload['max_tokens'] = min(int(payload.get('max_tokens', 8192)), 32768)
                if (payload.get('thinking') or {}).get('budget_tokens', 0) >= payload['max_tokens']:
                    payload['thinking']['budget_tokens'] = payload['max_tokens']-1
                bounded = dict(payload, stream=False)

                def send():
                    nonlocal begun
                    with requests.post('https://api.deepseek.com/anthropic/v1/messages',
                                       headers={'x-api-key': key, 'anthropic-version': '2023-06-01'},
                                       json=payload, timeout=(10, 120), allow_redirects=False, stream=True) as response:
                        if response.status_code != 200:
                            raise RuntimeError('DeepSeek HTTP '+str(response.status_code))
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream' if payload.get('stream') else 'application/json')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        begun = True
                        self.close_connection = True
                        chunks, total = [], 0
                        for chunk in response.iter_content(8192):
                            total += len(chunk)
                            if total > 8_000_000:
                                raise RuntimeError('Resposta demasiado grande')
                            chunks.append(chunk)
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        body = b''.join(chunks)
                        usage = stream_usage(body) if payload.get('stream') else _json_usage(body)
                        return dict(usage=usage)

                B.request(bounded, send, path=budget_path, necessity=necessity, reason=reason)
            except Exception as exc:
                if not begun:
                    # No request bodies, provider error bodies or credentials in logs.
                    detail = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
                    self.reply(429, json.dumps(dict(type='error', error=dict(type='budget_error', message=detail))).encode())

        def web_request(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 100000:
                    raise ValueError('Pedido demasiado grande')
                payload = json.loads(self.rfile.read(size))
                def send():
                    with requests.post('https://api.deepseek.com'+self.path,
                                       headers={'Authorization': 'Bearer '+key}, json=payload,
                                       timeout=(10, 45), allow_redirects=False, stream=True) as response:
                        chunks, total = [], 0
                        for chunk in response.iter_content(65536):
                            total += len(chunk)
                            if total > 2_000_000:
                                raise ValueError('Resposta demasiado grande')
                            chunks.append(chunk)
                        body = b''.join(chunks)
                        if response.status_code != 200:
                            raise RuntimeError('DeepSeek Web HTTP '+str(response.status_code))
                        return dict(body=body, usage=dict(prompt_tokens=0, completion_tokens=0))
                result = B.request(dict(model='deepseek-flash', max_tokens=1, messages=[]), send,
                                   path=budget_path, necessity=necessity, reason=reason, request_fee_usd=.00075)
                self.reply(200, result['body'])
            except Exception:
                self.reply(429, b'{"error":"DeepSeek web request unavailable or budget exhausted"}')
    return ThreadingHTTPServer(('127.0.0.1', 0), Handler)


def _json_usage(body):
    value = json.loads(body).get('usage') or {}
    return dict(prompt_tokens=value.get('input_tokens', 0)+value.get('cache_read_input_tokens', 0)+value.get('cache_creation_input_tokens', 0),
                completion_tokens=value.get('output_tokens'))


def main():
    import maestro as M
    key = M._extract_deepseek_key()
    if not key:
        raise RuntimeError('Credencial DeepSeek indisponível')
    credential = secrets.token_urlsafe(32)
    # Reserve escalation is host-controlled, never inferred from model output.
    server = gateway(key, credential, necessity=os.environ.get('MAESTRO_DEEPSEEK_NECESSITY', 'normal'),
                     reason=os.environ.get('MAESTRO_DEEPSEEK_REASON', ''))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, DEEPSEEK_BASE_URL=f'http://127.0.0.1:{server.server_port}/anthropic',
               DEEPSEEK_API_KEY=credential, DSH_TELEMETRY_MODE='DISABLED')
    try:
        # Final overlay wins over user/profile base URLs and model defaults.
        with tempfile.TemporaryDirectory(prefix='maestro-dsh-') as directory:
            overlay = pathlib.Path(directory)/'budget.json'
            model = os.environ.get('MAESTRO_DEEPSEEK_MODEL', 'deepseek-flash')
            if model not in ('deepseek-flash', 'deepseek-v4-pro'):
                raise ValueError('Modelo DeepSeek não validado')
            overlay.write_text(json.dumps([
                dict(id='llm-deepseek', config=dict(baseURL=env['DEEPSEEK_BASE_URL'], apiKeyEnv='DEEPSEEK_API_KEY', maxTokens=16384, reasoningEffort=os.environ.get('MAESTRO_DEEPSEEK_EFFORT', 'low'))),
                dict(id='agent-default-model', config=dict(provider='deepseek-official', model=model)),
                dict(id='session-title-llm', disabled=True),
                dict(id='llm-deepseek-account', disabled=True),
                dict(id='web-deepseek-api', config=dict(baseURL=f'http://127.0.0.1:{server.server_port}/v1/tools', apiKeyEnv='DEEPSEEK_API_KEY')),
                dict(id='web-search-deepseek', config=dict(baseURL=f'http://127.0.0.1:{server.server_port}/v1/tools', apiKeyEnv='DEEPSEEK_API_KEY')),
            ]))
            argv = sys.argv[2:]
            if len(argv) > 1 and argv[1] in ('headless', 'tui'):
                argv = [argv[0], '--profile', argv[1]] + argv[2:]
            return subprocess.call([argv[0], '--patch', str(overlay)] + argv[1:], env=env)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    sys.exit(main())
