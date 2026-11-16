"""Durable turn queue and conservative, quota-aware admission policy with bounded Sentinel triage."""
import json
import pathlib
import re
import threading
import time
import uuid
import unicodedata
from contextlib import contextmanager


MAX_PARALLEL_AGENTS = 4
MAX_RUNS_PER_ENGINE = 2  # simultaneous runs per provider family; a free equivalent plan is preferred before the 2nd


def scopes_overlap(left, right):
    """Unknown scopes cover everything; lists are unions of file/directory scopes."""
    if not left or not right:
        return True
    left = [left] if isinstance(left, str) else left
    right = [right] if isinstance(right, str) else right
    return any(a == b or a.startswith(b.rstrip('/') + '/') or
               b.startswith(a.rstrip('/') + '/') for a in left for b in right)


def write_paths(task, cwd):
    """Resolve explicit existing source files locally, never guess from topic names."""
    text = task.get('text') or ''
    if re.search(r'\b(agora|anterior|resultado|isso|isto|depois|previous|result|that|then|deploy|reinicia\w*|restart|instala\w*|install)\b', normalized(text)):
        return []
    root = pathlib.Path(cwd).resolve()
    paths = []
    for match in re.finditer(r'(?<![\w/])((?:/?[\w.@+-]+/)*[\w@+-]+\.(?:py|js|ts|jsx|tsx|css|html|json|yaml|yml|md|sh|toml))\b', text):
        path = (root / match[1]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return []
        paths.append(str(path))
    return sorted(set(paths))


def separate_writes(task, others):
    paths = task.get('write_paths')
    return bool(paths and others and all(
        (other.get('write_paths') or (other.get('plan') or {}).get('write_paths')) and
        not scopes_overlap(paths, other.get('write_paths') or (other.get('plan') or {}).get('write_paths'))
        for other in others))


class ExecutionSlots:
    """Shared bound for queue workers and workflow children; waiting is local."""
    def __init__(self):
        self.condition = threading.Condition()
        self.active = {}

    @contextmanager
    def hold(self, read_only, stopped=lambda: False, waiting=lambda: None, engine=None, scope=None):
        token = object()
        with self.condition:
            def blocked():
                for ro, group, path in self.active.values():
                    overlap = scopes_overlap(scope, path)
                    if overlap and (not read_only or not ro):
                        return True
                if engine and sum(g == family(engine) for _, g, _ in self.active.values()) >= MAX_RUNS_PER_ENGINE:
                    return True
                return len(self.active) >= MAX_PARALLEL_AGENTS
            while blocked():
                if stopped():
                    raise RuntimeError('Interrompido antes de obter capacidade de execução')
                waiting()
                self.condition.wait(.25)
            if stopped():
                raise RuntimeError('Interrompido antes de executar')
            self.active[token] = (read_only, family(engine) if engine else None, scope)
        try:
            yield
        finally:
            with self.condition:
                self.active.pop(token, None)
                self.condition.notify_all()


def execution_scope(plan, cwd):
    """What a run locks while it executes: explicit files, else the turn's own scope when the
    scheduler admitted it as an independent parallel writer, else the whole project folder."""
    return plan.get('write_paths') or plan.get('exec_scope') or str(pathlib.Path(cwd).resolve())


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if not unicodedata.combining(c))


MUTATION = r'\b(corrig\w*|altera\w*|implement\w*|apaga\w*|instala\w*|reinicia\w*|cria\w*|muda\w*|adicion\w*|acrescent\w*|arranja\w*|melhora\w*|publica\w*|faz\w*|fix|edit|change|write|delete|deploy|restart|create|add|build|improve)\b'


def wants_changes(text):
    """The request asks for work to be done, not only for an explanation."""
    return bool(re.search(MUTATION, normalized(text or '')))


def independent_analysis(task):
    text = normalized(task.get('text', ''))
    # A concrete standalone reading request need not contain the words "in parallel".
    # Contextual follow-ups and mutations still wait for their prerequisites.
    independent = re.search(r'\b(independent\w*|em paralelo|separadamente|outro assunto|separately|unrelated)\b', text)
    read_only = re.search(r'\b(analisa\w*|analis\w*|resume|resumo|compara\w*|explica\w*|leitura|analy[sz]\w*|summari[sz]\w*|compare|explain|read.only)\b', text)
    mutation = re.search(r'\b(corrig\w*|altera\w*|implement\w*|apaga\w*|instala\w*|reinicia\w*|cria\w*|muda\w*|fix|edit|change|write|delete|deploy|restart|create|run|execut\w*)\b', text)
    dependent = re.search(r'\b(depois|a seguir|resultado|anterior|isso|disso|isto|disto|aqui|agora|melhor|mais|mesmo|after|previous|result|same|that|then|this|above|again)\b', text)
    standalone = re.match(r'\s*(?:por favor[, ]+)?(?:explica\w*|compara\w*|resume|analisa\w*|explain|compare|summari[sz]e|analy[sz]e)\s+\S+(?:\s+\S+){2,}', text)
    # Plain questions that change nothing (no mutation verb, no "that/previous") can run on another engine meanwhile.
    question = text.strip().endswith('?') and not re.search(r'\b(se for|se estiver|podes|pode|consegues|integr\w*|publica\w*|merge|faz\w*|avanca\w*)\b', text)
    return bool((independent or task.get('separate_project') or standalone or question) and (read_only or question) and not mutation and not dependent and not task.get('images') and not task.get('docs'))


def executors(running):
    """Count actual workflow workers, rather than treating its coordinator as busy."""
    result = []
    for job in running:
        if job.get('workflow'):
            result.extend(c for c in job.get('children', []) if c.get('status') == 'running')
        else:
            result.append(job)
    return result


def family(engine):
    engine = str(engine or '')
    if engine in ('sonnet', 'opus', 'haiku', 'fable') or 'claude' in engine:
        return 'claude'
    if engine.startswith('codex'):
        return 'codex'
    if engine.startswith('deepseek'):
        return 'deepseek'
    return engine


def observe_daily(path, used, reset, now=None):
    """Persist observed daily consumption across processes/restarts.

    The first sample is a partial day; do not invent consumption before it.
    Days use the same 24h boundary as the weekly allowance.
    """
    import fcntl
    now = time.time() if now is None else now
    if not isinstance(used, (int, float)) or not isinstance(reset, (int, float)) or not 0 < reset - now <= 7 * 86400:
        return {}
    day = int((now - (reset - 7 * 86400)) // 86400)
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            old = json.loads(path.read_text())
        except (OSError, ValueError):
            old = {}
        if old.get('reset') == reset and old.get('day') == day:
            record = dict(old, used=max(old.get('used', used), used))
        else:
            # Only a near-boundary sample can claim a complete daily reading.
            boundary = reset - 7 * 86400 + day * 86400
            record = dict(reset=reset, day=day, baseline=used, used=used,
                          partial=now - boundary > 120)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(record))
        tmp.replace(path)
    return dict(daily_used_pct=max(0, record['used'] - record['baseline']),
                daily_measurement_partial=record['partial'])


def weekly_budget(q, now=None):
    """Local spending policy in provider quota percentage points, not token counts.

    Release one seventh per 24h day anchored to the provider's weekly reset.
    Unused allowance carries forward; already spent quota is never reset locally.
    """
    import math
    now = time.time() if now is None else now
    reset = q.get('seven_day_reset')
    used = q.get('seven_day_pct')
    if not isinstance(reset, (int, float)) or not isinstance(used, (int, float)) or not math.isfinite(reset) or not math.isfinite(used) or not 0 < reset - now <= 7 * 86400:
        return dict(daily_budget_pct=100 / 7, pace_target_pct=None,
                    pace_status='desconhecido', pace_blocked=True,
                    pace_available_pct=0, pace_resume_at=None)
    day = min(7, max(1, int((now - (reset - 7 * 86400)) // 86400) + 1))
    target = day * 100 / 7
    daily_exhausted = q.get('daily_used_pct', 0) >= 100 / 7
    blocked = used >= target or daily_exhausted
    next_day = min(7, int(used / (100 / 7)) + 1)
    resume = reset if used >= 100 else reset - 7 * 86400 + (next_day - 1) * 86400
    if daily_exhausted:
        resume = max(resume, reset - 7 * 86400 + day * 86400)
    return dict(daily_budget_pct=100 / 7, pace_target_pct=target,
                pace_status='acima_do_ritmo' if blocked else 'ok',
                pace_blocked=blocked, pace_available_pct=max(0, min(target - used, 100 / 7 - q.get('daily_used_pct', 0))),
                pace_resume_at=resume if blocked else None)


def pace_blocked(engine, quotas):
    if family(engine) != 'codex':
        return False
    return weekly_budget(quotas.get('codex', {}))['pace_blocked']


def essential_reserve(task, plan):
    # Conservative local evidence: explicit incident + advanced work, never an
    # image, manual model choice, "force", or complexity alone. No LLM spend.
    text = normalized(task.get('text', ''))
    incident = re.search(r'\b(?:perda de dados|corrupcao de dados|servico indisponivel|producao parada|data loss|data corruption|production outage)\b', text)
    urgent = re.search(r'\b(?:urgente|emergencia|agora|urgent|emergency|imediato)\b', text)
    action = re.search(r'\b(?:recupera\w*|restaura\w*|resolve\w*|corrige\w*|mitiga\w*|recover\w*|restore\w*|repair\w*|fix\w*)\b', text)
    return bool(incident and urgent and action and plan.get('dim_level', 2) >= 3)


def below_pace(engine, quotas):
    group = family(engine)
    q = quotas.get('antigravity' if group == 'agy' else group, {})
    if group == 'claude':
        return q.get('seven_day_pct', 100) < q.get('pace_target_pct', 0)
    if group == 'codex':
        reset = q.get('seven_day_reset')
        if not isinstance(reset, (int, float)):
            return False
        ideal = max(0, min(100, 100 * (1 - (reset-time.time()) / (7*86400))))
        return q.get('seven_day_pct', 100) < ideal
    if group == 'agy':
        return not q.get('limited', True) and q.get('gemini_used_pct', 100) < q.get('pace_target_pct', 0)
    return False


def decide(task, plan, running):
    """Return engine/reason. Never infer parallel writes from an unrelated topic."""
    workers = executors(running)
    if len(workers) >= MAX_PARALLEL_AGENTS:
        return None, 'capacity'
    if running and not task.get('parallel_ok') and not independent_analysis(task):
        return None, 'dependency'
    if any(j.get('chat_id') == task['chat_id'] and j.get('pending_before') for j in running):
        return None, 'dependency'
    q = plan.get('quotas', {})
    available = list(plan.get('available_engines', []))
    c, x, a, d = (q.get(k, {}) for k in ('claude', 'codex', 'antigravity', 'deepseek'))
    budget = d.get('budget') or {}
    # A critical incident may reach redline even after normal availability ended.
    # The execution adapter repeats the necessity check and logs every reservation.
    if budget.get('critical_available') and essential_reserve(task, plan):
        d['available'] = True
        if 'deepseek' not in available:
            available.append('deepseek')
        plan.setdefault('available_engines', [])[:] = list(dict.fromkeys(plan.get('available_engines', [])+['deepseek']))
    available = [e for e in available if not (
        (family(e) == 'codex' and (x.get('five_hour_est_pct', 100) >= 92 or x.get('seven_day_pct', 100) >= 100)) or
        (family(e) == 'claude' and (c.get('five_hour_pct', 100) >= 90 or c.get('seven_day_pct', 100) >= 100 or c.get('pace_status') == 'limitado')) or
        (e == 'agy' and a.get('limited', True)) or
        (e == 'deepseek' and not d.get('available', False))
    )]
    if budget and not budget.get('available', True):
        alternatives = [e for e in available if e != 'deepseek' and not pace_blocked(e, q)]
        justified = (not alternatives and
                     ((budget.get('reserve_available') and plan.get('dim_level', 2) >= 2) or
                      (budget.get('critical_available') and essential_reserve(task, plan))))
        if not justified:
            available = [e for e in available if e != 'deepseek']
    if (task.get('images') or
            (d.get('budget', {}).get('cli_allowed') is False and not
             (independent_analysis(task) or plan.get('read_only') or plan.get('no_tools')))):
        available = [e for e in available if e != 'deepseek']
    paced = [e for e in available if pace_blocked(e, q)]
    regular = [e for e in available if e not in paced]
    capable = ('codex', 'claude', 'agy', 'deepseek') if budget.get('cli_allowed') else ('codex', 'claude', 'agy')
    compatible = [e for e in regular if plan.get('dim_level', 2) < 3 or family(e) in capable]
    reserve = bool(paced and not compatible and essential_reserve(task, plan) and
                   weekly_budget(x)['pace_status'] != 'desconhecido')
    available = regular + (paced if reserve else [])
    requested = task.get('engine', 'auto')
    failed = task.get('failed_engines') or []  # one entry per failed attempt
    tried_fam = {family(e) for e in failed}
    if failed and requested == 'auto':
        # 2 attempts on the first engine, then up to 3 others (1 each), preferring another company/plan.
        if failed.count(failed[0]) < 2 and failed[0] in available:
            available = [failed[0]]
        else:
            available = [e for e in available if e not in failed]
            if not available:
                return None, 'exhausted'
    if requested != 'auto':
        candidates = [e for e in available if family(e) == family(requested)]
        if not candidates:
            return None, 'daily_budget' if paced else 'quota'
        candidates = [requested]
    else:
        preferred = plan.get('exec_engine') or plan.get('recommended_engine')
        cheap = plan.get('dim_level', 2) == 1 and not task.get('images') and not task.get('docs')
        pressure = x.get('five_hour_est_pct', 0) >= 75 or x.get('seven_day_pct', 0) >= 85
        long_context = task.get('estimated_input_tokens', 0) >= 6000
        economy = cheap or ((pressure or long_context) and plan.get('dim_level', 2) <= 2 and not task.get('images'))
        candidates = ([ 'deepseek' ] if economy else []) + [preferred] + available
        # Keep complex work on capable coding providers, allowing another subscription.
        if plan.get('dim_level', 2) >= 3:
            candidates = [e for e in candidates if family(e) in capable]
        # Use spare included quota before paid API; preserve specialty within each band.
        candidates = sorted(candidates, key=lambda e: not below_pace(e, q))
        candidates = [e for e in candidates if e in available]
        candidates = sorted(candidates, key=lambda e: family(e) in tried_fam)  # new company first
    runs = [family((j.get('plan') or {}).get('exec_engine', '')) for j in workers]
    # Prefer an idle equivalent plan (another provider) before a 2nd run on the same one;
    # non-capable providers (e.g. paid API) get a +1 penalty so quality is not traded blindly.
    def load(e):
        return runs.count(family(e)) + (0 if plan.get('dim_level', 2) < 2 or family(e) in capable or e == candidates[0] or independent_analysis(task) else 1)
    candidates = sorted(candidates, key=load)
    for engine in candidates:
        if runs.count(family(engine)) < MAX_RUNS_PER_ENGINE:
            return engine, ('essential_reserve' if engine in paced else (('parallel' if independent_analysis(task) and not task.get('force') else 'parallel_write') if running else ('economy' if engine == 'deepseek' else 'ready')))
    return None, ('engine_busy' if candidates else 'daily_budget' if paced else 'quota')


class TurnQueue:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.lock = threading.RLock()
        self.mutation_version = 0
        try:
            self.tasks = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.tasks = []

    def recover(self):
        with self.lock:
            for t in self.tasks:
                if t['status'] == 'running':
                    # Auto-resume after a restart/OOM; the workflow keeps completed nodes.
                    # ponytail: 2 automatic retries then manual, guards against a crash loop.
                    n = t.get('auto_resumes', 0)
                    if n < 2:
                        t.update(status='queued', reason='evaluating', auto_resumes=n + 1)
                    else:
                        t.update(status='interrupted', reason='interrupted')
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.tasks, ensure_ascii=False))
        tmp.replace(self.path)

    def add(self, body):
        with self.lock:
            # Client request id makes retries safe after a lost HTTP response.
            request_id = body.get('request_id')
            for t in self.tasks:
                if request_id and t.get('request_id') == request_id and t['chat_id'] == body['id']:
                    return dict(t), False
            task = {k: body.get(k) for k in ('text', 'images', 'docs', 'project', 'engine', 'request_id', 'model_id', 'effort', 'steer', 'force', 'steer_note')}
            task.update(id=uuid.uuid4().hex, chat_id=body['id'], ts=time.time(), status='queued', reason='evaluating')
            self.tasks.append(task)
            self.save()
            return dict(task), True

    def update(self, task_id, **values):
        with self.lock:
            for t in self.tasks:
                if t['id'] == task_id:
                    t.update(values)
                    self.save()
                    return dict(t)

    def edit_pending(self, chat_id, task_id, text, expected_text):
        with self.lock:
            task = next((t for t in self.tasks if t['id'] == task_id and t['chat_id'] == chat_id), None)
            if not task or task['status'] not in ('queued', 'interrupted'):
                raise ValueError('O pedido já saiu da fila. Atualiza antes de editar.')
            if task.get('text') != expected_text:
                raise ValueError('O pedido foi alterado noutra janela. Atualiza antes de editar.')
            if not isinstance(text, str) or (not text.strip() and not task.get('images') and not task.get('docs')):
                raise ValueError('Escreve um pedido ou mantém um anexo.')
            task.update(text=text.strip(), checked_at=0, blocked_by=None, waiting_on=None, conflict=None, force=False)
            if task['status'] == 'queued':
                task['reason'] = 'evaluating'
            self.mutation_version += 1
            self.save()

    def reorder_pending(self, chat_id, task_ids):
        with self.lock:
            slots = [i for i, t in enumerate(self.tasks) if t['chat_id'] == chat_id and t['status'] in ('queued', 'interrupted')]
            current = {self.tasks[i]['id']: self.tasks[i] for i in slots}
            if (not isinstance(task_ids, list) or any(not isinstance(k, str) for k in task_ids)
                    or len(task_ids) != len(current) or set(task_ids) != set(current)):
                raise ValueError('A fila mudou. Atualiza antes de reordenar.')
            for i, task_id in zip(slots, task_ids):
                self.tasks[i] = current[task_id]
            self.mutation_version += 1
            self.save()

    def pending(self, chat_id):
        with self.lock:
            return [dict(t) for t in self.tasks if t['chat_id'] == chat_id and t['status'] in ('queued', 'interrupted')]


POLICIES = ('fifo', 'chat', 'short', 'auto')


def order_pending(pending, positions, running_chats, policy):
    """Order in which waiting requests are offered to the scheduler. Within a chat the arrival order always holds.
    fifo = by arrival; chat = finish a chat before starting another; short = cheapest chat head first;
    auto = chats already running first, then cheapest head."""
    heads = {}
    for t in sorted(pending, key=lambda t: positions[t['id']]):
        heads.setdefault(t['chat_id'], t)
    def cost(t):
        return len(t.get('text') or '') + 2000 * len(t.get('images') or []) + 1000 * len(t.get('docs') or [])
    def key(t):
        h, pos = heads[t['chat_id']], positions[t['id']]
        if policy == 'chat':
            return (positions[h['id']], pos)
        if policy == 'short':
            return (cost(h), positions[h['id']], pos)
        if policy == 'auto':
            return (t['chat_id'] not in running_chats, cost(h), positions[h['id']], pos)
        return (pos,)
    return sorted(pending, key=key)


_TRIAGE_CACHE = {}
TRIAGE_PROMPT = (
    "Triagem de uma fila de pedidos a um assistente de sysadmin/programação. Pedido NOVO:\n<<{new}>>\n\n"
    "Pedidos ainda a correr ou à frente dele na fila:\n{others}\n\n"
    "O pedido NOVO pode correr JÁ em paralelo, noutro agente? Sim, se tratar de outra app, site, repositório "
    "ou serviço, ou se for só uma pergunta/leitura. Não, se depender da resposta/resultado dos outros "
    "(um 'avança', 'sim', 'e isso?', 'resolve', ou retoma o mesmo assunto) ou se alterar a mesma app, o mesmo "
    "repositório ou o mesmo serviço que eles. Partilhar o mesmo servidor ou disco não impede o paralelo. "
    "Responde só uma palavra: PARALELO ou ESPERAR."
)


def triage_independent(task, others, claude_bin, fallback=None):
    """Sentinel: Haiku → optional provider fallback → local wait; cache valid verdicts only."""
    import os, subprocess
    texts = [str(o.get('text') or '')[:400] for o in others]
    prompt = TRIAGE_PROMPT.format(new=(task.get('text') or '')[:600],
                                  others='\n'.join(f'- <<{t}>>' for t in texts))
    # Include text, not just IDs: edited requests need fresh classification.
    key = (task['id'], tuple(o.get('id') for o in others), prompt)
    if key in _TRIAGE_CACHE:
        return _TRIAGE_CACHE[key]
    if not texts or len(normalized(task.get('text', '')).split()) < 5:
        return False

    def remember(raw, engine):
        verdict = str(raw or '').strip().upper()
        if verdict not in ('PARALELO', 'ESPERAR'):
            return None
        result = verdict == 'PARALELO'
        # Bounded memory, and no sticky errors on provider outages.
        if len(_TRIAGE_CACHE) >= 512:
            _TRIAGE_CACHE.clear()
        _TRIAGE_CACHE[key] = result
        task['triage'] = dict(role='sentinel', engine=engine, verdict=verdict)
        return result

    try:
        out = subprocess.run([claude_bin, '-p', prompt, '--model', 'haiku', '--allowedTools', ''],
                             capture_output=True, text=True, timeout=20, cwd='/tmp',
                             env={k: v for k, v in os.environ.items() if k != 'CCA_TOKEN'})
        result = remember(out.stdout, 'haiku') if out.returncode == 0 else None
        if result is not None:
            return result
    except Exception:
        pass
    if fallback:
        try:
            result = remember(fallback(prompt), 'deepseek')
            if result is not None:
                return result
        except Exception:
            pass
    task['triage'] = dict(role='sentinel', engine='local', verdict='ESPERAR', reason='providers_unavailable')
    return False  # Do not cache outages: the next scheduler pass may retry.
