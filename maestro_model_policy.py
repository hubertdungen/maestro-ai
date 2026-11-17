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


# Tabela de preferências do Hubert: o planeador recebe-a inteira; cada subtarefa recebe
# só as regras cujo gatilho aparece no pedido (também no plano de reserva).
ROUTING_TABLE = """TABELA DE PREFERÊNCIAS (tipo de tarefa → papel · motor · esforço)
| Tarefa | role | Motor preferido | Esforço |
|---|---|---|---|
| Inventário, procurar ficheiros, ler logs | scout | haiku / economy | low |
| Arquitetura, decisões que outras tarefas seguem | architecture | opus | high |
| Código geral, scripts, NAS, testes, debug | implementation | codex (sol) | medium |
| Frontend visual premium (galeria, portfólio, landing, 3D, animação) | implementation | opus | high |
| Ajustes de UI pequenos e delimitados | implementation | sonnet | medium |
| Gerar/editar imagens, mockups, recortes | implementation | codex (image_gen.imagegen) | medium |
| Curadoria de imagens com visão | implementation | codex ou opus | medium |
| QA visual (capturas reais vs critérios) | review | opus | high |
| Revisão de código exigente | review | sonnet ou codex terra | high |
| Batch/processamento em massa | implementation | deepseek | — |
Quota apertada num grupo: troca para o motor equivalente de outro grupo antes de baixar o esforço de arquitetura ou QA."""

QUALITY_RULES = (
    (r'galeria|gallery|portf[oó]lio|landing|\bsite|website|p[aá]gina|\bui\b|\bux\b|react|\b3d\b|anima|design|layout|visual',
     'VISUAL: entrega de nível profissional, comparável aos melhores templates React atuais; '
     'reescrita pedida = não reaproveitar o layout antigo. «3D» = profundidade real (camadas, parallax e tilt ao rato, '
     'transições), não uma grelha plana. Valida com tools/ver.py a 1440/820/390 e ABRE as capturas antes de declarar feito.'),
    (r'imagem|imagens|image|foto|mockup|render|galeria|gallery|produto|portf[oó]lio',
     'IMAGENS: curadoria com visão (folha de contacto, ver cada imagem): 3–6 melhores por projeto, sem repetidas, '
     'desfocadas ou de processo sem interesse; o produto final tem de aparecer (procura em 3DCults, hubertdungen.com, '
     'cloud4tb). Mockups/edições: Codex com image_gen.imagegen e referenced_image_paths das fotos reais; nunca o '
     'gerador Pollinations do Maestro. Guarda nos assets do projeto e regista num manifesto o que entra e porquê.'),
    (r'arquitet|architect|estrutura|stack|reescre|refactor',
     'ARQUITETURA: abre o código e os ativos reais antes de decidir (nada de decisões às cegas); esforço high.'),
    (r'podre|fraco|feio|nada profissional|continua (?:igual|mal)|outra vez|por amor de deus|n[aã]o (?:gostei|est[aá] bom)|falta de',
     'INSATISFAÇÃO: transforma cada queixa do Hubert num critério de aceitação verificável e confirma-o um a um '
     'no QA; sobe o nível do motor/esforço em relação à tentativa anterior.'),
)


def guidance(text):
    """Regras de qualidade aplicáveis ao pedido (vazio se nenhuma)."""
    t = (text or '').casefold()
    rules = [rule for pattern, rule in QUALITY_RULES if re.search(pattern, t)]
    return ('\n[REGRAS DE QUALIDADE DO HUBERT]\n- ' + '\n- '.join(rules) + '\n') if rules else ''
