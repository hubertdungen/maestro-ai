"""Explicit, single-use approvals for shell blocks already stored in a chat.

Runs in a separate systemd unit so a deployment can restart the web service.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path.home() / '.local/state/maestro/actions'
SHELL_BLOCK = re.compile(r'```(?:bash|sh|shell)\n([\s\S]*?)```', re.I)


def write(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.chmod(0o600)
    tmp.replace(path)


def action_path(action_id):
    if not re.fullmatch(r'[a-f0-9]{32}', str(action_id)):
        raise ValueError('Ação inválida')
    return ROOT / (action_id + '.json')


def preview(chat, code, cwd):
    code = str(code)
    if not code.strip() or len(code) > 64000:
        raise ValueError('Comando vazio ou demasiado grande')
    matches = [m for m in chat.get('messages', []) if m.get('role') == 'assistant'
               and code in SHELL_BLOCK.findall(m.get('text', ''))]
    if not matches:
        raise ValueError('O comando tem de constar de uma resposta guardada do assistente')
    # Identical approvals remain single-use, including after a page reload.
    key = hashlib.sha256((chat['id'] + '\0' + str(cwd) + '\0' + code).encode()).hexdigest()[:32]
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = action_path(key)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            return status(key)
        data = dict(id=key, chat_id=chat['id'], code=code, cwd=str(Path(cwd).resolve()),
                    status='pending', created_at=time.time())
        write(path, data)
    return data


def status(action_id):
    path = action_path(action_id)
    data = json.loads(path.read_text())
    log = path.with_suffix('.log')
    if log.exists():
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size - 24000))
            data['output'] = stream.read().decode('utf-8', 'replace')
    return data


def decide(action_id, approve):
    path = action_path(action_id)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text())
        if data['status'] != 'pending':
            return status(action_id)
        if time.time() - data['created_at'] > 3600:
            data['status'] = 'expired'
        elif not approve:
            data['status'] = 'cancelled'
        else:
            data.update(status='starting', approved_at=time.time())
            write(path, data)
            try:
                result = subprocess.run([
                    'systemd-run', '--user', '--collect', '--quiet',
                    '--unit=maestro-action-' + action_id,
                    '--property=RuntimeMaxSec=1800',
                    sys.executable, str(Path(__file__).resolve()), action_id,
                ], capture_output=True, text=True, timeout=10)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip())
            except Exception as exc:
                data.update(status='failed', error=str(exc), finished_at=time.time())
                write(path, data)
            return status(action_id)
        write(path, data)
    return status(action_id)


def run(action_id):
    path = action_path(action_id)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text())
        if data['status'] != 'starting':
            return
        data.update(status='running', started_at=time.time())
        write(path, data)
    try:
        with path.with_suffix('.log').open('wb') as log:
            os.chmod(log.name, 0o600)
            proc = subprocess.Popen(['/bin/bash', '-e', '-o', 'pipefail', '-c', data['code']],
                                    cwd=data['cwd'], stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                rc = proc.wait(timeout=1740)
                data.update(status='succeeded' if rc == 0 else 'failed', exit_code=rc)
            except subprocess.TimeoutExpired:
                import signal
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                data.update(status='failed', error='Tempo limite de 29 minutos excedido')
    except Exception as exc:
        data.update(status='failed', error=str(exc))
    data['finished_at'] = time.time()
    write(path, data)


if __name__ == '__main__':
    run(sys.argv[1])
