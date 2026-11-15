"""Bounded local telemetry and conservative feedback. Never invokes a model.

Character estimates describe only Maestro-supplied text, excluding provider system
prompts, tool loops and image tokens. A no-Maestro run is a counterfactual, not a
measurement; no total-token savings are asserted.
"""
import fcntl
import json
import pathlib
import re
import time
import unicodedata
from contextlib import contextmanager

STATE = pathlib.Path.home() / '.local/state/maestro/efficiency.json'
LIMIT = 200


def estimate(text):
    return (len(str(text)) + 3) // 4


def usage(raw, provider):
    if not isinstance(raw, dict):
        return None
    inp, out = raw.get('input_tokens', raw.get('prompt_tokens')), raw.get('output_tokens', raw.get('completion_tokens'))
    if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in (inp, out)):
        return None
    # Claude reports cache tokens separately; Codex input already includes cached input.
    cached = [raw.get(k, 0) or 0 for k in ('cache_read_input_tokens', 'cache_creation_input_tokens')] if provider == 'claude' else []
    if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in cached):
        return None
    cache = sum(cached)
    return dict(input_tokens=inp + cache, output_tokens=out, total_tokens=inp + cache + out)


@contextmanager
def ledger(path=None, write=True):
    path = pathlib.Path(path or STATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.exists() else {'calls': [], 'turns': []}
        if (not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in ('calls', 'turns')) or
                any(not isinstance(row, dict) for k in ('calls', 'turns') for row in data[k])):
            raise ValueError('Invalid efficiency ledger')
        original = json.dumps(data, ensure_ascii=False) if write else None
        yield data
        if not write or json.dumps(data, ensure_ascii=False) == original:
            return
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(path)


def record_call(prompt, result, plan, engine, path=None):
    # No prompt, answer, credentials or file contents are persisted.
    row = dict(ts=time.time(), turn_id=plan.get('turn_id'), engine=engine,
               role='planning' if plan.get('no_tools') else plan.get('task_role', 'implementation'),
               supplied_input_estimate=estimate(prompt), usage=result.get('usage'), rc=result.get('rc', 1))
    with ledger(path) as data:
        data['calls'] = (data['calls'] + [row])[-LIMIT:]
    return row


def category(plan):
    return str(plan.get('domain', 'unknown')) + ':' + str(plan.get('dim_level', 2))


def local_plan_reason(text, plan, readonly_engines, path=None):
    value = ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if not unicodedata.combining(c))
    if re.search(r'\b(multi.agente|multi.agent|subtarefas|subtasks|deleg\w*|divide\w*)\b', value):
        return None  # Explicit delegation always retains a planner.
    if not readonly_engines:
        return 'Plano local: só há um executor compatível; evita planeamento sem divisão possível.'
    if plan.get('dim_level', 2) >= 3:
        return None
    try:
        with ledger(path, write=False) as data:
            recent = [t for t in data['turns'] if t['category'] == category(plan)][-10:]
        planned = [t for t in recent if t['source'] != 'local_feedback']
        if (len(planned) >= 3 and all(t['ok'] for t in recent) and
                all(t['nodes'] == 1 and t['input_overhead_ratio'] >= .10 for t in planned[-3:])):
            # Re-evaluate with a planner after at most six consecutive local turns.
            trailing_local = 0
            for turn in reversed(recent):
                if turn['source'] != 'local_feedback':
                    break
                trailing_local += 1
            if trailing_local >= 6:
                return None
            return 'Plano local: três execuções únicas bem sucedidas com overhead ≥10%; avaliação sem modelo.'
        return None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def evaluate_turn(plan, full_prompt, calls, final, source, path=None):
    supplied = sum(c['supplied_input_estimate'] for c in calls)
    baseline = estimate(full_prompt)
    report = dict(category=category(plan), ok=final['status'] == 'done', nodes=len(final['nodes']),
                  source=source, calls=len(calls), supplied_input_estimate=supplied,
                  direct_input_estimate=baseline, input_overhead_estimate=max(0, supplied-baseline),
                  input_overhead_ratio=max(0, supplied-baseline)/max(1, baseline),
                  measured_total_tokens=sum(c['usage']['total_tokens'] for c in calls if c.get('usage')),
                  measured_calls=sum(bool(c.get('usage')) for c in calls),
                  without_maestro_total_tokens=None, total_savings_tokens=None,
                  note='Comparação apenas do texto enviado; ferramentas, imagens e contexto interno excluídos. Sem execução de controlo, poupança total desconhecida.')
    # Resume with no calls must not train the policy or pretend to save tokens.
    if calls:
        with ledger(path) as data:
            data['turns'] = (data['turns'] + [dict(report, ts=time.time())])[-LIMIT:]
    return report


def snapshot(path=None):
    try:
        with ledger(path, write=False) as data:
            calls, turns = data['calls'], data['turns']
    except (OSError, ValueError, TypeError, KeyError):
        return dict(error='Telemetria indisponível; execução normal preservada')
    try:
        return dict(evaluation='local; zero chamadas a modelos', window_limit=LIMIT,
                calls=len(calls), measured_calls=sum(bool(c.get('usage')) for c in calls),
                measured_total_tokens=sum(c['usage']['total_tokens'] for c in calls if c.get('usage')),
                last_evaluation=turns[-1] if turns else None,
                total_savings_tokens=None, baseline='Contrafactual; poupança total não medida')

    except (KeyError, TypeError, ValueError):
        return dict(error='Telemetria indisponível; execução normal preservada')
