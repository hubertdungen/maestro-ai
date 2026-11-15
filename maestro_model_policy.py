"""Local routing preferences, not provider benchmarks or entitlement claims."""
import re


def profile(entry):
    model = entry.get('model', '').lower()
    engine = entry.get('engine')
    if engine == 'codex':
        if 'luna' in model:
            tier, purpose = 'economy', 'Pesquisa curta, explicações e alterações simples delimitadas.'
        elif 'astra' in model:
            tier, purpose = 'advanced', 'Raciocínio difícil, arquitetura e diagnóstico com várias hipóteses.'
        elif 'terra' in model:
            tier, purpose = 'advanced', 'Código complexo, refactor e revisão técnica exigente.'
        else:
            tier, purpose = 'standard', 'Implementação geral, testes, debug e operações no NAS.'
    elif engine in ('sonnet', 'opus', 'haiku', 'fable'):
        tier, purpose = {
            'haiku': ('economy', 'Triagem, leitura curta e tarefas simples.'),
            'sonnet': ('standard', 'Frontend, UI/UX, implementação e revisão corrente.'),
            'opus': ('advanced', 'Arquitetura, refactor profundo e revisão difícil.'),
            'fable': ('reserve', 'Alternativa explícita; sem preferência automática de especialidade validada.'),
        }[engine]
    elif engine == 'agy':
        if 'flash' in model:
            tier, purpose = 'economy', 'Triagem e pesquisa curta; visão apenas se suportada pelo adaptador.'
        elif 'hydrogen' in model or 'opus' in model:
            tier, purpose = 'advanced', 'Análise e coordenação técnica de pedidos complexos.'
        elif 'gpt-oss' in model:
            tier, purpose = 'reserve', 'Alternativa de texto/código; visão não confirmada.'
        else:
            tier, purpose = 'standard', 'Gestão de agentes, pesquisa e implementação geral.'
    elif entry.get('transport') == 'deepseek-api':
        tier = 'economy' if 'flash' in model else 'advanced'
        purpose = 'Scout de ficheiros e análise de texto, com ferramentas limitadas a leitura.'
    else:
        tier = 'advanced' if 'pro' in model else 'standard'
        purpose = 'Raciocínio e arquitetura difíceis quando justificam o custo.' if 'pro' in model else 'Flash: scripts, batch e implementação corrente com custo controlado.'
    group = 'claude-max' if engine in ('sonnet', 'opus', 'haiku', 'fable') else (
        'codex-plus' if engine == 'codex' else ('deepseek-balance' if engine == 'deepseek' else 'antigravity'))
    return dict(tier=tier, purpose=purpose, quota_group=group,
                policy_source='Preferência local de encaminhamento; não é um benchmark.',
                # Conservative transport gate. Do not infer vision from a brand name.
                vision=engine in ('codex', 'sonnet', 'opus', 'haiku', 'fable') or
                       (engine == 'agy' and model.startswith('gemini-')))


def role_for(plan, text=''):
    if plan.get('no_tools'):
        return 'planning'
    if plan.get('task_role'):
        return plan['task_role']
    if plan.get('read_only'):
        return 'scout'
    if plan.get('dim_level', 2) >= 3:
        return 'complex'
    return 'simple' if plan.get('dim_level') == 1 else 'implementation'


def ranked(entries, role='implementation', read_only=False, images=False, engine=None):
    target = ('economy' if role in ('planning', 'analysis', 'scout', 'simple') else
              'advanced' if role in ('complex', 'architecture') else 'standard')
    def score(e):
        p = profile(e)
        model = e.get('model', '')
        # Stable preference for the current general coding model, retaining older
        # catalogue entries as alternatives when an ID is rejected.
        generation = 0 if '6.1' in model else 1 if '6-' in model else 2
        return (p['tier'] != target, p['tier'] == 'reserve',
                p['tier'] == 'advanced' if target != 'advanced' else False,
                generation, 'sol' not in model, model)
    return sorted((e for e in entries if e.get('available', True) and e.get('automatic_available', True)
                   and (not engine or e['engine'] == engine)
                   and (not read_only or e.get('read_only_enforced'))
                   and (read_only or e.get('transport') != 'deepseek-api')
                   and (not images or profile(e)['vision'])), key=score)


def effort_for(entry, role):
    levels = entry.get('efforts') or []
    wanted = 'low' if role in ('planning', 'analysis', 'scout', 'simple') else 'high' if role in ('complex', 'architecture', 'review') else 'medium'
    return wanted if wanted in levels else entry.get('default_effort')


def rejection_reason(rc, stdout, stderr=''):
    """Retry only an explicit provider rejection, never a failed/partial task."""
    if not rc:
        return None
    pattern = r'(?:quota (?:exceeded|exhausted)|usage limit|rate[_ ]limit|orçamento diário/mensal insuficiente|hit your.*limit|insufficient[_ ](?:quota|balance)|individual quota reached|resource_exhausted|model[^\n]*(?:not found|not available|not supported|does not exist|access denied)|unsupported model)'
    evidence = (stderr or '') + '\n' + (stdout or '')
    if re.search(r'(?:\nexec\n|\btool[_ ](?:call|use|result)\b|\b(?:apply_patch|functions\.exec|shell_command)\b)', evidence, re.I):
        return None
    if not re.search(pattern, evidence, re.I):
        return None
    # Nonempty output must itself be just a rejection. Any substantive result
    # or tool transcript blocks replay of possible writes.
    lines = [line.strip() for line in (stdout or '').splitlines() if line.strip()]
    if any(not re.search(pattern, line, re.I) for line in lines):
        return None
    return 'model' if re.search(r'unsupported model|model[^\n]*(?:not found|not available|not supported|does not exist|access denied)', evidence, re.I) else 'quota'
