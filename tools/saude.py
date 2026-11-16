#!/usr/bin/env python3
"""Saúde do Maestro, medida nos dados reais (conversas, fila, execuções), não em promessas.

    python3 tools/saude.py              # últimos 3 dias, comparados com os 3 anteriores
    python3 tools/saude.py --dias 7
    python3 tools/saude.py --desde "2026-11-16 12:00"   # desde um momento (ex.: um deploy) até agora

Cada linha é um indicador que se quer BAIXO, exceto os marcados com ↑.
"""
import argparse
import collections
import datetime as dt
import glob
import json
import pathlib
import re
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro as M
import maestro_qagate as G

STATE = M.STATE_DIR


def load():
    chats = []
    for path in glob.glob(str(STATE / 'chats' / '*.json')):
        try:
            chats.append(json.loads(pathlib.Path(path).read_text()))
        except (OSError, ValueError):
            pass
    try:
        queue = json.loads((STATE / 'turn-queue.json').read_text())
    except (OSError, ValueError):
        queue = []
    history = []
    try:
        for line in (STATE / 'history.jsonl').read_text().splitlines():
            try:
                history.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return chats, queue, history


def pct(part, whole):
    return None if not whole else 100.0 * part / whole


def measure(chats, queue, history, start, end):
    inside = lambda ts: isinstance(ts, (int, float)) and start <= ts < end
    replies = [m for c in chats for m in c.get('messages', []) if m.get('role') == 'assistant' and inside(m.get('ts'))]
    asks = [m for c in chats for m in c.get('messages', []) if m.get('role') == 'user' and inside(m.get('ts'))
            and not str(m.get('text') or '').startswith('[')]
    buttons = [m for c in chats for m in c.get('messages', []) if m.get('role') == 'user' and inside(m.get('ts'))
               and str(m.get('text') or '').startswith('[')]
    turns = [t for t in queue if inside(t.get('ts'))]
    runs = [h for h in history if inside(h.get('ts'))]
    waits = [t['started_at'] - t['ts'] for t in turns if t.get('started_at')]
    count = lambda group: sum(1 for m in replies if any(re.search(p, G._OK.sub('', str(m.get('text') or '')), re.I | re.M) for p in group))
    asking = sum(1 for m in replies if any(re.search(p, str(m.get('text') or '')[-600:], re.I | re.S) for p in G._ASK))
    failures = collections.Counter(h['worker'].split(' (')[0].replace('Motor · ', '') for h in runs if h.get('rc'))
    by_engine = collections.Counter(h['worker'].split(' (')[0].replace('Motor · ', '') for h in runs)
    reasons = collections.Counter(r for t in turns for cycle in (t.get('qa_reasons') or []) for r in cycle)
    return {
        'pedidos teus': len(asks),
        'respostas': len(replies),
        'respostas que acabam a pedir autorização (%)': pct(asking, len(replies)),
        'respostas que declaram trabalho por acabar (%)': pct(count(G._NEG), len(replies)),
        'respostas sem ver o resultado / «Ctrl+F5» (%)': pct(count(G._UNSEEN), len(replies)),
        'respostas a dizer que não fecharam o git (%)': pct(count(G._GIT), len(replies)),
        'pedidos teus a dizer que falhou (%)': pct(sum(1 for m in asks if M.says_it_failed(str(m.get('text') or ''))), len(asks)),
        'cliques em botões «continuar/aplicar»': len(buttons),
        'turnos corrigidos pela porta (%)': pct(sum(1 for t in turns if t.get('qa_cycles')), len(turns)),
        'turnos que desistiram': sum(1 for t in turns if t.get('gave_up')),
        'espera na fila, mediana (s)': statistics.median(waits) if waits else None,
        'espera na fila, 9 em cada 10 abaixo de (s)': sorted(waits)[int(len(waits) * .9) - 1] if len(waits) >= 10 else None,
        'execuções falhadas (%)': pct(sum(failures.values()), len(runs)),
        '_motores': dict(by_engine.most_common(6)),
        '_falhas': dict(failures.most_common(4)),
        '_motivos da porta': dict(reasons.most_common(5)),
    }


def fmt(value):
    if value is None:
        return '—'
    return f'{value:.0f}' if isinstance(value, float) else str(value)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dias', type=float, default=3)
    ap.add_argument('--desde', help='"AAAA-MM-DD HH:MM": mede desde aí até agora e compara com o mesmo intervalo antes')
    a = ap.parse_args()
    now = dt.datetime.now().timestamp()
    start = dt.datetime.fromisoformat(a.desde).timestamp() if a.desde else now - a.dias * 86400
    span = now - start
    data = load()
    current, before = measure(*data, start, now), measure(*data, start - span, start)
    stamp = lambda ts: dt.datetime.fromtimestamp(ts).strftime('%d/%m %H:%M')
    print(f'Saúde do Maestro · {stamp(start)} → {stamp(now)}   (antes: {stamp(start - span)} → {stamp(start)})\n')
    width = max(len(k) for k in current if not k.startswith('_'))
    print(f'{"indicador".ljust(width)}  {"antes":>7}  {"agora":>7}')
    for key in current:
        if key.startswith('_'):
            continue
        print(f'{key.ljust(width)}  {fmt(before[key]):>7}  {fmt(current[key]):>7}')
    for key in ('_motores', '_falhas', '_motivos da porta'):
        if current[key]:
            print(f'\n{key[1:]} (agora): ' + ', '.join(f'{k} {v}' for k, v in current[key].items()))
    if current['respostas'] < 20:
        print(f'\nAtenção: só {current["respostas"]} respostas no período; ainda não chega para tirar conclusões.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
