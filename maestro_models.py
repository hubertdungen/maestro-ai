"""Provider-discovered model catalogue. Discovery never sends an inference request.

A listed model is selectable, not a promise of current account entitlement. Quota
checks are separate and repeated by the turn scheduler before starting work.
"""
import copy
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import threading
import time
import maestro_model_policy as POLICY

HOME = pathlib.Path.home()
# O serviço web pode arrancar com um PATH mínimo (systemd, cron, ttyd). O
# catálogo não pode concluir "não instalado" só porque `which` não encontra o
# CLI: usa os mesmos caminhos absolutos que o executor do Maestro.
NODE_BIN = HOME / '.local/ai-cli-tools/node/current/bin'
NPM_BIN = HOME / '.local/ai-cli-tools/npm-global/bin'
LOCAL_BIN = HOME / '.local/bin'
_CACHE = None
_CACHE_AT = 0
_LOCK = threading.RLock()
_SANDBOX_READONLY = None


def _binary(name):
    """Resolve um CLI por PATH e, se falhar, pelos caminhos reais do NAS."""
    found = shutil.which(name)
    if found:
        return found
    for base in (NPM_BIN, LOCAL_BIN, NODE_BIN):
        candidate = base / name
        if candidate.exists():
            return str(candidate)
    return ''


def _run(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return p.stdout if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def _entry(engine, model, label, efforts, default, source, note=''):
    return dict(id=engine+':'+model, engine=engine, model=model, label=label,
                efforts=efforts, default_effort=default, source=source,
                available=True, note=note,
                read_only_enforced=engine in ('codex', 'sonnet', 'opus', 'haiku', 'fable'))


def _sandbox_readonly_available():
    global _SANDBOX_READONLY
    if _SANDBOX_READONLY is None:
        try:
            codex = _binary('codex')
            if not codex:
                _SANDBOX_READONLY = False
            else:
                p = subprocess.run([codex, 'sandbox', '-c', 'sandbox_mode="read-only"', '--', '/usr/bin/true'],
                                   capture_output=True, timeout=8)
                _SANDBOX_READONLY = p.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _SANDBOX_READONLY = False
    return _SANDBOX_READONLY


def _deepseek_api_models():
    from maestro_readonly import discover_models
    return discover_models()


def _discover():
    entries, notes = [], []
    codex_path = HOME / '.codex/models_cache.json'
    codex = _binary('codex')
    if codex:
        try:
            cached = json.loads(codex_path.read_text())
            for m in cached.get('models', []):
                if m.get('visibility') != 'list' or not re.fullmatch(r'[a-zA-Z0-9_.-]+', m.get('slug', '')):
                    continue
                efforts = [e['effort'] for e in m.get('supported_reasoning_levels', []) if isinstance(e, dict) and isinstance(e.get('effort'), str)]
                e = _entry('codex', m['slug'], m.get('display_name') or m['slug'], efforts,
                           m.get('default_reasoning_level'), 'codex/models_cache.json',
                           'Catálogo local da conta; acesso confirmado apenas ao executar.')
                e['catalogue_fetched_at'] = cached.get('fetched_at')
                e['read_only_enforced'] = _sandbox_readonly_available()
                if not e['read_only_enforced']:
                    e['note'] += ' Sandbox de leitura indisponível neste NAS.'
                entries.append(e)
        except (OSError, ValueError, TypeError):
            notes.append('Catálogo Codex indisponível; não foram inventados modelos ou efforts.')
    else:
        notes.append('CLI Codex não encontrado; nenhum modelo OpenAI foi presumido.')
    claude = _binary('claude')
    if claude:
        help_text = _run([claude, '--help'])
        efforts_match = re.search(r'--effort\s+<level>\s+Effort level.*?\(([^)]+)\)', help_text, re.S)
        # Help confirms this is a CLI option; individual models may restrict it.
        efforts = [s.strip() for s in efforts_match.group(1).split(',')] if efforts_match else []
        for alias in ('sonnet', 'opus', 'haiku', 'fable'):
            # Haiku is also a built-in alias in the installed CLI binary.
            if '--model' in help_text and (alias in help_text or alias == 'haiku'):
                e = _entry(alias, alias, 'Claude '+alias.title(), efforts, None, 'claude --help / installed CLI',
                           'Alias do CLI; esforços expostos pelo CLI, compatibilidade depende do modelo e da conta.')
                entries.append(e)
        if not any(e['engine'] in ('sonnet', 'opus', 'haiku', 'fable') for e in entries):
            notes.append('CLI Claude encontrado, mas não anunciou aliases; nenhum modelo foi presumido.')
    else:
        notes.append('CLI Claude não encontrado; nenhum modelo Anthropic foi presumido.')
    agy = _binary('agy')
    if agy:
        text = _run([agy, 'models'])
        for line in text.splitlines():
            parts = line.strip().split('\t', 1)
            if len(parts) != 2 or not re.fullmatch(r'[a-zA-Z0-9_.-]+', parts[0]):
                continue
            entries.append(_entry('agy', parts[0], parts[1], [], None, 'agy models',
                                  'O nível de esforço já faz parte deste ID; quota verificada separadamente.'))
        if not any(e['engine'] == 'agy' for e in entries):
            notes.append('Antigravity não devolveu catálogo; nenhum ID Gemini foi presumido.')
    else:
        notes.append('CLI Antigravity (agy) não encontrado; nenhum ID Gemini foi presumido.')
    dsh = _binary('dsh')
    if dsh:
        entries.append(_entry('deepseek', 'configured', 'DeepSeek · perfil configurado', [], None,
                              'dsh --profile headless --help',
                              'O CLI headless não expõe --model nem --effort; usa a configuração do perfil.'))
    else:
        notes.append('CLI DeepSeek (dsh) não encontrado; o perfil CLI não foi listado.')
    for m in _deepseek_api_models():
        if dsh and m['id'] in ('deepseek-flash', 'deepseek-v4-pro'):
            entries.append(_entry('deepseek', m['id'], m['id']+' · CLI controlado',
                                  ['off', 'low', 'high', 'max'], 'low',
                                  'https://api-docs.deepseek.com/quick_start/pricing/',
                                  'Gateway local; custo reservado por chamada, 15 EUR/ciclo e margem diária justificada.'))
        effort = m.get('effort') or {}
        e = _entry('deepseek', m['id'], (m.get('name') or m['id'])+' · só leitura API',
                   ['none']+effort.get('supported_levels', []), 'none',
                   'GET api.deepseek.com/models',
                   'Ferramentas Python limitadas a leitura; sem shell, execução, escrita ou URLs livres.')
        e.update(id='deepseek-api:'+m['id'], transport='deepseek-api', read_only_enforced=True)
        entries.append(e)
    for entry in entries:
        entry.update(POLICY.profile(entry))
    return dict(models=entries, notes=notes, fetched_at=time.time())


def catalog(quotas=None, refresh=False):
    """Return cached discovery plus optional current quota availability (5 min TTL)."""
    global _CACHE, _CACHE_AT
    with _LOCK:
        if refresh or _CACHE is None or time.time() - _CACHE_AT > 300:
            _CACHE = _discover()
            _CACHE_AT = time.time()
        result = copy.deepcopy(_CACHE)
    if quotas is not None:
        for e in result['models']:
            family = 'claude' if e['engine'] in ('sonnet', 'opus', 'haiku', 'fable') else ('antigravity' if e['engine'] == 'agy' else e['engine'])
            q = quotas.get(family, {})
            blocked = ((family == 'codex' and (q.get('limit_reached') or q.get('five_hour_est_pct', 0) >= 92 or q.get('seven_day_pct', 0) >= 100)) or
                       (family == 'claude' and (q.get('limited') or q.get('five_hour_pct', 0) >= 90 or q.get('seven_day_pct', 0) >= 100)) or
                       (family == 'antigravity' and (q.get('limited') or (q.get('models_3p_limited') and
                                                     e['id'].split(':', 1)[-1].startswith(('claude', 'gpt'))))) or
                       (family == 'deepseek' and q.get('available') is False))
            if family == 'deepseek' and e.get('transport') != 'deepseek-api' and q.get('budget', {}).get('cli_allowed') is False:
                blocked = True
                e['note'] = 'CLI sem teto verificável; usar API com orçamento. '+e['note']
            if family == 'deepseek' and q.get('budget', {}).get('available') is False:
                e['automatic_available'] = False
            if blocked:
                e.update(available=False, note='Quota do fornecedor indisponível. '+e['note'])
            if e['engine'] == 'opus':
                e['automatic_available'] = (q.get('pace_status', 'ok') == 'ok' and
                                            q.get('five_hour_pct', 0) < 65 and q.get('seven_day_pct', 0) < 88)
    return result


def resolve(model_id, effort=None, quotas=None):
    """Validate exact catalogue IDs; never silently switch model or effort."""
    entry = next((e for e in catalog(quotas)['models'] if e['id'] == model_id), None)
    if entry is None:
        raise ValueError('Modelo não consta do catálogo local')
    if not entry['available']:
        raise ValueError('Modelo indisponível devido à quota do fornecedor')
    if effort and effort not in entry['efforts']:
        raise ValueError('Nível de esforço não suportado por este modelo')
    return dict(entry, effort=effort or entry['default_effort'])


def readonly_claude_args():
    """Read-only Claude worker: file tools plus inspection commands (git history/diff and the page checker).

    dontAsk denies everything that is not pre-approved, so writes and any other command stay blocked.
    """
    here = pathlib.Path(__file__).resolve().parent
    files = 'Read,Glob,Grep,WebSearch,WebFetch'
    shell = ','.join('Bash(%s:*)' % c for c in ('python3 ' + str(here / 'tools' / 'inspect_repo.py'),
                                               'python3 ' + str(here / 'tools' / 'ver.py')))
    return ['--tools', files + ',Bash', '--allowedTools', files + ',' + shell,
            '--add-dir', '/srv/cloud4tb/WORK/DEVELOPMENT', '--add-dir', str(here),
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--permission-mode', 'dontAsk']


def readonly_claude_prompt(cwd):
    here = pathlib.Path(__file__).resolve().parent
    command = 'python3 ' + shlex.quote(str(here / 'tools' / 'inspect_repo.py'))
    return ('\n\n[PERMISSÕES REAIS DESTA SUBTAREFA DE LEITURA]\n'
            'Esta etapa de pesquisa/revisão tem ferramentas restritas; a implementação tem um âmbito separado. '
            'Usa Read para ficheiros (incluindo o contexto do workflow), Glob para listar e Grep para pesquisar. '
            'WebSearch e WebFetch estão disponíveis. Bash permite apenas os comandos de inspeção abaixo.\n'
            'Git: ' + command + ' status ' + shlex.quote(cwd) + '\n'
            'Troca status por log, diff, staged ou branches para outras inspeções; '
            'passa o caminho do repositório como último argumento.\n'
            'Página renderizada: python3 ' + str(here / 'tools' / 'ver.py') + ' URL --texto "frase"\n'
            'Executa cada comando diretamente, sem cd, timeout, pipes, redirecionamentos ou comandos encadeados. '
            'Não uses Bash para cat/head/tail/find/rg; usa as ferramentas de ficheiros. '
            'Uma recusa de um comando não significa que Bash ou o Maestro inteiro estejam bloqueados. '
            'Usa a ferramenta permitida adequada e continua a pesquisa; se faltar uma ação de escrita, '
            'passa essa necessidade ao implementador sem pedir autorização ao utilizador.\n')


def command_config(selection, read_only=False):
    """Additional argv only; caller must remove existing model/sandbox flags.

    Never combine these with bypass-permissions flags for read-only workers.
    AGY/dsh lack a verified read-only preset here, so refuse those tasks.
    """
    e = resolve(selection['id'], selection.get('effort'))
    engine, model, effort = e['engine'], e['model'], e['effort']
    if e.get('transport') == 'deepseek-api':
        raise ValueError('Modelo API exige o adaptador maestro_readonly, não um CLI')
    args = []
    if engine == 'codex':
        args = ['-m', model]
        if effort:
            args += ['-c', 'model_reasoning_effort='+json.dumps(effort)]
        if read_only:
            args += ['-s', 'read-only', '-c', 'approval_policy="never"']
    elif engine in ('sonnet', 'opus', 'haiku', 'fable'):
        args = ['--model', model]
        if effort:
            args += ['--effort', effort]
        if read_only:
            # dontAsk nega tudo o que não esteja pré-aprovado (e pastas fora do projeto): aprovar só leitura
            # e dar as mesmas pastas que o modo de escrita, senão o scout fica «Bloqueado».
            args += readonly_claude_args()
    elif engine == 'agy':
        args = ['--model', model]
    if read_only and not e['read_only_enforced']:
        raise ValueError('Fornecedor sem restrição de leitura verificada; não executar em paralelo')
    return args
