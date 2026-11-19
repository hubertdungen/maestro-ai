"""Durable, provider-neutral task graphs; all dependency waits happen in Python.

Planning costs one optional ``planner(prompt)`` call. Workers receive only the
objective and outputs of their dependencies. No coordinator process remains open
while workers run. A restarted process never silently replays an interrupted task.
"""
from __future__ import annotations

import concurrent.futures
import copy
import json
import pathlib
import re
import threading
import time
import unicodedata
import uuid

from maestro_queue import MAX_PARALLEL_AGENTS
import maestro_model_policy as POLICY

MAX_NODES = 6
TERMINAL = {'done', 'failed', 'blocked', 'interrupted', 'cancelled'}


def mixed_request(text):
    """Cheap trigger only; the planner decides whether the parts are independent."""
    value = ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if not unicodedata.combining(c))
    # Ignore questions inside fenced examples.
    value = re.sub(r'```[\s\S]*?```', '', value)
    question = re.search(r'[^?\n]+\?', value)
    if not question:
        return False
    action = r'\b(?:corrig\w*|implement\w*|adicion\w*|acrescent\w*|altera\w*|arranja\w*|cria\w*|fix|implement|add|change|build)\b'
    # A lone "can you implement X?" is one task. Require a separate clause.
    rest = value[question.end():]
    before = value[:question.start()]
    return bool(re.search(action, rest) or re.search(action, before) or
                re.search(action + r'[^?]*(?:ja agora|alem disso|e tambem|also|and separately)', question.group()))


def needs_workflow(text, plan=None):
    """Use explicit multi-task requests and broad improvement work, not every turn."""
    plan = plan or {}
    value = ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if not unicodedata.combining(c))
    broad = re.search(r'\b(melhora\w*|improve\w*|refactor\w*|refatora\w*)\b', value)
    inspect = re.search(r'\b(verifica\w*|analisa\w*|investiga\w*|review|audit\w*|ve|check|inspect\w*)\b', value)
    explicit = re.search(r'\b(multi.agente|multi.agent|subtarefas|subtasks|divide\w*.*tarefas|deleg\w*)\b', value)
    return bool(mixed_request(text) or explicit or (broad and inspect) or (plan.get('dim_level', 0) >= 3 and plan.get('multi_agent')))


def validate_plan(plan, allowed_engines):
    if not isinstance(plan, dict):
        raise ValueError('O plano tem de ser um objeto JSON.')
    raw = plan.get('nodes')
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_NODES:
        raise ValueError('O plano deve ter entre 1 e 6 tarefas.')
    allowed = set(allowed_engines)
    nodes = []
    ids = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('Tarefa inválida.')
        nid = item.get('id')
        if not isinstance(nid, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', nid) or nid in ids:
            raise ValueError('Identificador de tarefa inválido ou repetido.')
        ids.add(nid)
        engine = item.get('engine')
        if engine not in allowed and isinstance(engine, str) and ':' in engine and engine.split(':')[0] in allowed:
            # O planeador às vezes escreve o model_id («haiku:haiku») no lugar do motor.
            item.setdefault('model_id', engine)
            engine = engine.split(':')[0]
        if engine not in allowed:
            raise ValueError(f'Motor não disponível: {engine}')
        read_only = item.get('read_only')
        if not isinstance(read_only, bool):
            raise ValueError('read_only deve ser booleano explícito.')
        role = item.get('role', 'analysis' if read_only else 'implementation')
        if role == 'analysis' and re.search(r'arquitet|architect', f"{nid} {item.get('title', '')}", re.I):
            role = 'architecture'
        if role not in ('scout', 'analysis', 'architecture', 'implementation', 'review', 'synthesis'):
            raise ValueError('Papel de agente inválido.')
        if role in ('scout', 'analysis', 'architecture', 'review', 'synthesis') and not read_only:
            raise ValueError('Pesquisa, análise, revisão e síntese são apenas de leitura.')
        deps = item.get('depends_on', [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps) or len(set(deps)) != len(deps):
            raise ValueError('Dependências inválidas.')
        instruction = item.get('instruction')
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 12000:
            raise ValueError('Instrução de tarefa inválida.')
        title = item.get('title', nid)
        if not isinstance(title, str) or not title.strip() or len(title) > 180:
            raise ValueError('Título de tarefa inválido.')
        nodes.append(dict(id=nid, title=title, instruction=instruction, engine=engine,
                          read_only=read_only, role=role, depends_on=list(deps)))
    for node, item in zip(nodes, raw):
        for key in ('model_id', 'effort'):
            if item.get(key):
                if not isinstance(item[key], str) or len(item[key]) > 180:
                    raise ValueError('Seleção de modelo/esforço inválida')
                node[key] = item[key]
    for node in nodes:
        if not node['read_only'] and POLICY.touches_ue(node['title'] + ' ' + node['instruction']):
            engine = POLICY.ue_engine(node['engine'], allowed_engines)
            if engine != node['engine']:
                node.update(engine=engine); node.pop('model_id', None); node.pop('effort', None)
    by_id = {n['id']: n for n in nodes}
    visited, visiting = set(), set()

    def visit(nid):
        if nid not in by_id or nid in visiting:
            raise ValueError('Dependência inexistente ou ciclo no plano.')
        if nid in visited:
            return
        visiting.add(nid)
        for dep in by_id[nid]['depends_on']:
            visit(dep)
        visiting.remove(nid)
        visited.add(nid)
    for nid in by_id:
        visit(nid)
    return {'version': 1, 'reason': str(plan.get('reason') or 'Tarefas com dependências explícitas.')[:2000], 'nodes': nodes}


def parse_plan(text, allowed_engines):
    value = text.strip()
    if value.startswith('```'):
        value = re.sub(r'^```(?:json)?\s*', '', value)
        value = re.sub(r'\s*```$', '', value)
    return validate_plan(json.loads(value), allowed_engines)


def default_plan(objective, engines, preferred=None):
    engines = list(dict.fromkeys(engines))
    if not engines:
        raise ValueError('Nenhum motor disponível para planear.')
    main = preferred if preferred in engines else engines[0]
    if POLICY.touches_ue(objective):
        main = POLICY.ue_engine(main, engines)
    scout = next((e for e in ('deepseek', 'agy', 'haiku', 'claude-sonnet') if e in engines), engines[0])
    reviewer = next((e for e in engines if e != main), main)
    return validate_plan({'reason': 'Pesquisa → implementação → revisão; espera local sem chamadas ao modelo.', 'nodes': [
        {'id': 'scout', 'title': 'Investigar e propor alterações', 'role': 'scout', 'engine': scout, 'read_only': True,
         'depends_on': [], 'instruction': 'Investiga o pedido e o código relevante. Não alteres ficheiros nem executes ações mutáveis. Entrega caminhos, problemas confirmados, riscos e uma proposta concreta de implementação. Não delegues novamente.'},
        {'id': 'implement', 'title': 'Implementar e validar', 'role': 'implementation', 'engine': main, 'read_only': False,
         'depends_on': ['scout'], 'instruction': 'Implementa o pedido original usando a pesquisa recebida. Respeita o âmbito autorizado. Executa as verificações adequadas e entrega ficheiros alterados, resultados e limitações reais. Não delegues novamente.'},
        {'id': 'review', 'title': 'Rever e entregar resultado', 'role': 'review', 'engine': reviewer, 'read_only': True,
         'depends_on': ['scout', 'implement'], 'instruction': 'Revê as alterações e as verificações do implementador através de inspeção apenas de leitura. Não alteres ficheiros nem executes ações mutáveis. Se estiver tudo certo, apresenta o resultado final ao utilizador. Se encontrares problemas, lista-os de forma concreta e acionável (ficheiro, linha, o que corrigir): o implementador corrige-os a seguir, neste mesmo turno. Nunca declares concluído algo não confirmado. Não delegues novamente.'},
    ]}, engines)


def planner_prompt(objective, engines, preferred=None):
    return ('És o Maestro responsável pela gestão de agentes: distribuis quem faz o quê e propões um esboço inicial. '
            'Este papel não te torna automaticamente o arquiteto ou executor da missão. '
            'Decide se este pedido beneficia de 1 a 6 tarefas. '
            'Responde APENAS com JSON {"reason":"justificação breve", "nodes":[{"id":"nome", '
            '"title":"tarefa", "instruction":"instruções concretas", "engine":"motor", '
            '"role":"scout|analysis|architecture|implementation|review|synthesis", "read_only":true, "depends_on":[]}]} . '
            '"engine" é só o nome do motor (ex.: "opus"); o modelo concreto vai em "model_id". '
            'Usa apenas estes motores: ' + json.dumps(list(engines)) + '. Preferido para implementação: ' + str(preferred) + '. '
            'Escolhe modelos de acordo com a especialidade, as ferramentas do motor e a complexidade. '
            'A hierarquia de substituição do Maestro não é uma prioridade de execução nem de arquitetura. '
            'Se a complexidade o justificar, atribui uma tarefa analysis de arquitetura/coordenação técnica '
            'ao modelo disponível mais adequado, eventualmente mais capaz que o gestor. '
            'Dá-lhe autoridade para rever o esboço técnico preservando o objetivo do utilizador; '
            'faz as implementações dependerem dessa análise e seguirem as decisões técnicas recebidas. '
            'Em pedidos mistos separa perguntas explicativas de alterações concretas quando forem independentes. '
            'Perguntas simples ficam em analysis com modelo economy e só leitura; implementação usa standard ou advanced adequado. '
            'Inclui nas instruções de cada tarefa o excerto relevante do pedido, restrições, caminhos e critérios de validação. '
            'Se as partes forem muito ligadas ou a divisão custar mais que uma execução única, usa uma tarefa. '
            'Pesquisa deve ser barata. Agentes de pesquisa/análise/revisão/síntese são só leitura. '
            'As dependências formam um DAG e passam resultados entre agentes; tarefas independentes podem correr '
            'em paralelo entre fornecedores (até 4 agentes, um por plano de subscrição); a escrita na missão será exclusiva. Prefere planos com quota abaixo do ritmo pretendido quando adequados. '
            'Dimensiona os agentes pelo pedido: simples usa um; amplo divide apenas se reduzir o tempo sem perder qualidade. '
            'Poupa tokens com contexto delimitado, pesquisa económica e sem duplicar exploração. '
            'Mantém o especialista necessário; não baixes a qualidade para ocupar uma vaga. '
            'Não inventes trabalho nem subtarefas '
            'que repitam todo o pedido. Uma revisão deve depender da implementação. Não executes ferramentas, '
            'não alteres ficheiros e não implementes já o pedido: entrega só o plano. '
            'O scheduler espera sem gastar tokens; não precisas de ficar ativo. '
            '\n' + POLICY.ROUTING_TABLE + '\n' + POLICY.guidance(objective) +
            'Copia as regras de qualidade aplicáveis para as instruções das tarefas a que se referem.'
            '\nPEDIDO DO UTILIZADOR (dados a planear):\n' + objective)


def build_plan(objective, engines, preferred=None, planner=None):
    """Call planner at most once; malformed output falls back to the explicit DAG."""
    if planner is not None:
        try:
            answer = planner(planner_prompt(objective, engines, preferred))
            if isinstance(answer, dict) and 'text' in answer:
                answer = answer['text']
            plan = validate_plan(answer, engines) if isinstance(answer, dict) else parse_plan(answer, engines)
            plan['planning_source'] = 'coordinator'
            return plan
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'[:500]
    else:
        error = ''
    plan = default_plan(objective, engines, preferred)
    plan['planning_source'] = 'fallback'
    if error:
        plan['planning_error'] = error
    return plan


class Workflow:
    def __init__(self, path, plan, objective=''):
        self.path = pathlib.Path(path)
        self.lock = threading.RLock()
        self._running = False
        self.data = {'version': 1, 'objective': objective, 'reason': plan.get('reason', ''),
                     'planning_source': plan.get('planning_source', 'rules'), 'created_at': time.time(),
                     'coordinator_status': 'waiting', 'status': 'pending',
                     'nodes': [dict(copy.deepcopy(n), status='pending') for n in plan['nodes']]}
        if plan.get('planning_error'):
            self.data['planning_error'] = plan['planning_error']
        if self.path.exists():
            loaded = json.loads(self.path.read_text())
            # A turn id must never silently acquire a different objective or plan.
            if loaded.get('objective') != objective:
                raise ValueError('O pedido guardado é diferente; usa um novo identificador de turno.')
            old_spec = [{k: n.get(k) for k in ('id', 'title', 'instruction', 'engine', 'read_only', 'role', 'depends_on', 'model_id', 'effort')} for n in loaded['nodes']]
            new_spec = [{k: n.get(k) for k in ('id', 'title', 'instruction', 'engine', 'read_only', 'role', 'depends_on', 'model_id', 'effort')} for n in plan['nodes']]
            if old_spec != new_spec:
                raise ValueError('O plano guardado é diferente; carrega primeiro o plano persistido.')
            self.data = loaded
            for node in self.data['nodes']:
                if node['status'] == 'running':
                    node.update(status='interrupted', error='Processo interrompido; repetir exige ação explícita.', finished_at=time.time())
            self.data['coordinator_status'] = 'waiting'
        self._save()

    @classmethod
    def load(cls, path):
        value = json.loads(pathlib.Path(path).read_text())
        plan = {k: value.get(k) for k in ('version', 'reason', 'planning_source', 'planning_error')}
        plan['nodes'] = [{k: n.get(k) for k in ('id', 'title', 'instruction', 'engine', 'read_only', 'role', 'depends_on', 'model_id', 'effort')} for n in value['nodes']]
        return cls(path, plan, value.get('objective', ''))

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data['updated_at'] = time.time()
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
        tmp.replace(self.path)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def guide(self, node_id, text, request_id=None):
        """Durable subchat input. Running workers consume it at their next boundary."""
        text = str(text or '').strip()
        if not text or len(text) > 6000:
            raise ValueError('A orientação deve ter entre 1 e 6000 caracteres.')
        with self.lock:
            node = next((n for n in self.data['nodes'] if n['id'] == node_id), None)
            if not node:
                raise ValueError('Subtarefa não encontrada.')
            messages = node.setdefault('guidance', [])
            if request_id and any(m.get('request_id') == request_id for m in messages):
                return self.snapshot()
            if self.data['status'] != 'running' or node['status'] not in ('pending', 'running'):
                raise ValueError('Esta subtarefa já terminou ou está interrompida; usa a conversa principal.')
            if len(messages) >= 30:
                raise ValueError('Limite de orientações desta subtarefa atingido.')
            messages.append(dict(id=uuid.uuid4().hex, request_id=request_id, text=text,
                                 ts=time.time(), status='queued'))
            self._save()
            return self.snapshot()

    def append(self, node):
        """Extend a finished plan with one more task (e.g. the maker closing review findings)."""
        with self.lock:
            if self._running:
                raise ValueError('Não é possível acrescentar tarefas enquanto o fluxo está ativo.')
            ids = {n['id'] for n in self.data['nodes']}
            if node['id'] in ids or any(d not in ids for d in node.get('depends_on', [])):
                raise ValueError('Tarefa repetida ou dependência inexistente.')
            self.data['nodes'].append(dict(copy.deepcopy(node), status='pending'))
            self.data.update(status='pending', coordinator_status='waiting')
            self.data.pop('finished_at', None)
            self._save()

    def retry(self, node_id):
        """An explicit retry retains completed siblings and dependency results."""
        with self.lock:
            if self._running:
                raise ValueError('Não é possível repetir tarefas enquanto o fluxo está ativo.')
            node = next((n for n in self.data['nodes'] if n['id'] == node_id), None)
            if not node or node['status'] not in ('failed', 'interrupted', 'cancelled', 'blocked'):
                raise ValueError('Esta tarefa não está disponível para repetição.')
            node.update(status='pending')
            for key in ('error', 'result', 'started_at', 'finished_at'):
                node.pop(key, None)
            # Blocked descendants may become ready again; independent failures stay failed.
            for child in self.data['nodes']:
                if child['status'] == 'blocked':
                    child.update(status='pending')
                    child.pop('error', None)
            self.data.update(status='pending', coordinator_status='waiting')
            self._save()

    def reroute(self, allowed, engine):
        """On resume, work still to run leaves engines that are no longer available."""
        with self.lock:
            for node in self.data['nodes']:
                if node['status'] != 'done' and node['engine'] not in allowed:
                    node['engine'] = engine
                    if not node.get('read_only') and POLICY.touches_ue(node.get('title', '') + ' ' + node.get('instruction', '')):
                        node['engine'] = POLICY.ue_engine(engine, allowed)
                    node.pop('model_id', None)  # belonged to the previous engine
                    node.pop('effort', None)
            self._save()

    def run(self, execute, stopped=lambda: False, on_update=lambda snapshot: None):
        """execute(node, context) -> dict; on_update receives every durable change.

        The callback must enforce read_only using provider permissions/tools, not
        merely trust this scheduling label. Cancellation callbacks should stop any
        active provider subprocesses. Scheduler cancellation does not replay them.
        """
        with self.lock:
            if self._running:
                raise RuntimeError('O fluxo já está ativo.')
            self._running = True
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_AGENTS, len(self.data['nodes'])), thread_name_prefix='maestro-task')
        active = {}

        def publish():
            self._save()
            on_update(self.snapshot())

        try:
            with self.lock:
                self.data.update(status='running', coordinator_status='waiting')
                publish()
            while True:
                with self.lock:
                    nodes = self.data['nodes']
                    by_id = {n['id']: n for n in nodes}
                    if stopped():
                        for node in nodes:
                            if node['status'] in ('pending', 'running'):
                                node.update(status='interrupted' if node['status'] == 'running' else 'cancelled',
                                            error='Interrompido pelo utilizador.', finished_at=time.time())
                        self.data.update(status='cancelled', coordinator_status='stopped')
                        publish()
                        for future in active:
                            future.cancel()
                        return self.snapshot()
                    changed = False
                    # Propagate failure through all descendants, regardless of node order.
                    while True:
                        blocked = [n for n in nodes if n['status'] == 'pending' and
                                   any(by_id[d]['status'] in ('failed', 'blocked', 'interrupted', 'cancelled') for d in n['depends_on'])]
                        if not blocked:
                            break
                        for node in blocked:
                            node.update(status='blocked', error='Aguarda resolução de uma dependência que não terminou.', finished_at=time.time())
                        changed = True
                    for future in list(active):
                        if not future.done():
                            continue
                        node = active.pop(future)
                        try:
                            result = future.result()
                            if not isinstance(result, dict) or not isinstance(result.get('text'), str):
                                raise ValueError('O agente não devolveu texto de resultado.')
                            json.dumps(result)  # Verify serializability before committing state.
                            node.update(result=result, actual_agent=result.get('agent_used', node['engine']))
                            # Failed provider results must not release dependent writers.
                            if result.get('failed') or result.get('ok') is False or result.get('status') in ('failed', 'interrupted', 'stopped'):
                                raise RuntimeError(result.get('text') or 'Falha no agente.')
                            node.update(status='done', result=result, actual_agent=result.get('agent_used', node['engine']))
                            node.setdefault('exchanges', []).append(dict(text=result['text'],
                                agent_used=result.get('agent_used', node['engine']), ts=time.time()))
                            # User input received during execution is handled before releasing dependants.
                            if any(m['status'] == 'queued' for m in node.get('guidance', [])):
                                node['status'] = 'pending'
                        except Exception as exc:
                            node.update(status='failed', error=f'{type(exc).__name__}: {exc}'[:2000])
                        node['finished_at'] = time.time()
                        changed = True
                    for node in nodes:
                        if node['status'] != 'pending' or any(by_id[d]['status'] != 'done' for d in node['depends_on']):
                            continue
                        if len(active) >= MAX_PARALLEL_AGENTS:
                            break
                        # Mutations are exclusive across the entire workflow.
                        if active and (not node['read_only'] or any(not n['read_only'] for n in active.values())):
                            continue
                        context = {'objective': self.data['objective'], 'dependency_results': {
                            dep: copy.deepcopy(by_id[dep].get('result') or {}) for dep in node['depends_on']}}
                        for result in context['dependency_results'].values():
                            result['text'] = result.get('text', '')[-16000:]
                        context['previous_result'] = copy.deepcopy(node.get('result') or {})
                        context['guidance'] = copy.deepcopy(node.get('guidance', []))
                        for message in node.get('guidance', []):
                            if message['status'] == 'queued':
                                message.update(status='delivered', delivered_at=time.time())
                        node.update(status='running', started_at=time.time())
                        # Persist before launching to prevent restart from replaying an uncertain write.
                        publish()
                        future = pool.submit(execute, copy.deepcopy(node), context)
                        active[future] = node
                        changed = True
                    if changed:
                        publish()
                    if not active and all(n['status'] in TERMINAL for n in nodes):
                        states = {n['status'] for n in nodes}
                        final = 'done' if states == {'done'} else ('interrupted' if 'interrupted' in states else 'failed')
                        self.data.update(status=final, coordinator_status='done', finished_at=time.time())
                        publish()
                        return self.snapshot()
                # Waiting consumes no model tokens and makes no provider request.
                if active:
                    concurrent.futures.wait(tuple(active), timeout=0.1, return_when=concurrent.futures.FIRST_COMPLETED)
                elif not changed:
                    raise RuntimeError('Plano sem tarefas executáveis; dependências inválidas.')
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            with self.lock:
                self._running = False
