"""Quality gate: a reply that says "not ready / must-fix / pending / tell me where" is not delivered; it goes back to the dev loop."""
import os
import pathlib
import re
import subprocess
import time

MAX_CYCLES = 3
VER = pathlib.Path(__file__).resolve().parent / 'tools' / 'ver.py'

# One contract for every writer: main turns, workflow makers and gate redo passes.
CONTRACT = (
    '[CONTRATO DE ENTREGA DO MAESTRO]\n'
    '- Tens shell completa nesta execução (git, gh, docker, systemctl --user, curl). Commit, push, PR, merge, deploy e reinício '
    'do serviço fazem parte da entrega sempre que o pedido os implica: faz-os neste turno, sem pedir licença.\n'
    '- Só três coisas pedem confirmação ao Hubert: apagar dados que este trabalho não criou, gastar dinheiro, enviar algo a terceiros. '
    'Em qualquer outra dúvida assume o default razoável, executa e diz qual foi.\n'
    '- Vê o resultado antes de o afirmar. Alteração visível (site, app, UI): corre '
    '`python3 ' + str(VER) + ' URL --texto "o que tem de aparecer" [--falta "o que tem de sair"] [--movel]`; grava uma captura PNG '
    '(abre-a) e confirma o texto na página renderizada. Alteração de serviço: consulta a porta/endpoint real depois do deploy.\n'
    '- O Hubert usa sobretudo o telemóvel: nunca lhe peças Ctrl+F5, Ctrl+Shift+R ou limpar cache como prova. Confirma tu o que o servidor serve.\n'
    '- Nunca reinicies o maestro-web: mata os pedidos dos outros chats que estão a correr. O código Python do Maestro '
    'recarrega sozinho quando os turnos terminam e os ficheiros ui/* são servidos do disco de imediato.\n'
    '- O histórico é o git: não cries cópias .bak nem ficheiros «-old»; faz commit do que funciona.\n'
    '- A resposta diz o que ficou «Feito e verificado» com a evidência (comando, URL, commit) e, só se existir, «Bloqueado» '
    '(bloqueio real fora do teu alcance e o que tentaste). Sem lista de pendentes opcionais e sem pergunta de permissão no fim.\n')

FINISH = ('Fecha a entrega. Recebes o trabalho do implementador e os achados da revisão: corrige TODOS os problemas apontados, '
          'repete a validação com ferramentas reais (testes, execução, tools/ver.py para o que é visível) e conclui commit, push, '
          'PR/merge e deploy quando o pedido os implica. A tua resposta é a que o Hubert vai ler: o resultado final, não um relatório de revisão.')
_NEG = (r'n[aã]o est[aá] pronto', r'n[aã]o (?:est[aá]|foi|ficou) (?:validad|testad|conclu[ií]d|publicad)\w*', r'n[aã]o validad\w*',
        r'must[- ]?fix', r'bloqueiam? (?:a |o )?(?:exposi|deploy|entrega)\w*', r'lacunas?\s+(?:funcionais|em aberto|por)', r'ficou(?:\s+por|\s+ainda por)\s+fazer',
        r'veredicto\s*:\s*(?:n[aã]o|reprovad|com ressalvas)', r'^\s*pendente\b', r'\bpendentes?\s*:', r'ainda falta\w*')
_ASK = (r'diz[- ]?me\s+(?:onde|qual|se|como|quando)', r'queres que\b', r'quer que\b', r'aguardo\b', r'posso avan[cç]ar', r'avan[cç]o\s+(?:com|para)\b.*\?',
        r'deixo (?:isso |o resto )?(?:para|à tua)')
# "Take my word for it": asking the user to defeat a cache, or admitting nobody looked at the page.
_UNSEEN = (r'(?:faz|fa[cç]a|carrega|prime|pressiona|experimenta|usa)\w*[^.\n]{0,40}ctrl\s*\+\s*(?:shift\s*\+\s*)?(?:f5|r)\b',
           r'limpa\w* (?:a |o )?cache',
           r'n[aã]o (?:vi|verifiquei|confirmei|testei|abri)\b[^.\n]{0,60}(?:browser|navegador|renderizad\w*)')
_OK = re.compile(r'(?:nada|nenhum[ao]?|sem|zero|0)\s+(?:\w+\s+)?(?:pendente|must-?fix|lacuna|bloqueio)\w*', re.I)


def check(text):
    """Return a list of matched reasons (empty = acceptable to deliver)."""
    t = _OK.sub('', str(text or ''))
    hits = [p for p in _NEG + _UNSEEN if re.search(p, t, re.I | re.M)]
    tail = t[-600:]  # permission questions only matter where the reply ends
    hits += [p for p in _ASK if re.search(p, tail, re.I | re.S)]
    return hits


_REVIEW = (r'n[aã]o aprovad\w*', r'reprovad\w*', r'rejeitad\w*', r'aprovad\w* com (?:ressalvas|reservas)', r'(?:por|a) corrigir\b',
           r'n[aã]o (?:consegui|pude|foi poss[ií]vel) (?:validar|verificar|testar|correr|executar|confirmar)',
           r'(?:sem|n[aã]o tinha|n[aã]o tenho) (?:acesso a )?(?:shell|browser|terminal)')


def review_open(text):
    """A reviewer's handoff that still needs a maker: findings, a negative verdict, or validation it could not run."""
    t = str(text or '')
    return check(t) + [p for p in _REVIEW if re.search(p, _OK.sub('', t), re.I)]


_SOURCE = {'.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.css', '.scss', '.html', '.md', '.sh', '.yml', '.yaml', '.toml',
           '.cs', '.vue', '.svelte', '.sql', '.go', '.rs', '.php', '.lua'}


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root)] + list(args), capture_output=True, text=True, timeout=15)


def repo_findings(text, cwd, since, request=''):
    """What the turn really left behind: files it changed and did not commit, commits it made and did not push.

    Repositories are the project folder plus every path the reply mentions. Only changes made
    after ``since`` count, so work in progress that was already there is never blamed on the turn.
    """
    if re.search(r'(?:n[aã]o|sem)\s+(?:\w+\s+){0,3}(?:commit\w*|push|merge|publi\w+)', request or '', re.I):
        return []  # the user asked for it to stay local
    roots = []
    for raw in [str(cwd or '')] + re.findall(r'(?<![\w:/.])(?:~|/[\w.@+-]+)(?:/[\w.@+-]+)+', str(text or '')):
        path = pathlib.Path(os.path.expanduser(raw.rstrip('.,;:)`')))
        try:
            while path != path.parent and not (path / '.git').exists():
                path = path.parent
            if (path / '.git').exists() and path not in roots:
                roots.append(path)
        except OSError:
            continue
    found = []
    for root in roots[:6]:
        try:
            status = _git(root, 'status', '--porcelain')
            if status.returncode:
                continue
            dirty = []
            for line in status.stdout.splitlines():
                name = line[3:].split(' -> ')[-1].strip().strip('"')
                if pathlib.Path(name).suffix.lower() not in _SOURCE:
                    continue  # databases, logs and app state change by themselves while a service runs
                try:
                    if (root / name).stat().st_mtime >= since:
                        dirty.append(name)
                except OSError:
                    pass  # deleted or unreadable: cannot be dated, never blamed
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(since))
            ahead = _git(root, 'log', '@{u}..HEAD', '--since=' + stamp, '--oneline')
            unpushed = ahead.stdout.splitlines() if ahead.returncode == 0 else []  # no upstream: local-only repo
        except (OSError, subprocess.SubprocessError):
            continue
        parts = []
        if dirty:
            parts.append('%d ficheiro(s) alterado(s) neste turno por commitar (%s)' % (len(dirty), ', '.join(dirty[:5])))
        if unpushed:
            parts.append('%d commit(s) deste turno por enviar (push)' % len(unpushed))
        if parts:
            found.append('%s: %s' % (root, '; '.join(parts)))
    return found


def redo_note(text, cycle, repos=()):
    body = str(text or '').strip()[-3500:]
    if repos:
        body += ('\n---\nEstado real dos repositórios, verificado pelo Maestro depois da tua resposta:\n' +
                 '\n'.join('- ' + r for r in repos) + '\nSe fazem parte deste trabalho, fecha: commit, push e o merge/deploy que o pedido implica. '
                 'Se não são teus, diz na resposta que os deixaste como estavam.')
    return ('\n\n[PORTA DE QUALIDADE · CICLO %d/%d · a entrega anterior foi RECUSADA pelo Maestro]\n' % (cycle, MAX_CYCLES) +
            'A revisão/resposta anterior declarou o trabalho incompleto:\n---\n' + body + '\n---\n'
            'Isto não é um relatório para o Hubert: é a tua lista de trabalho. Implementa TODOS os must-fix, lacunas funcionais e melhorias razoáveis, '
            'valida (testes/execução real), e só então responde. Não divides em fases, não termines com perguntas de permissão; '
            'se faltar um dado (ex.: onde fazer o deploy), assume o default razoável, executa e diz qual. '
            'Verifica primeiro o estado real (git status, ficheiros, serviço): o trabalho anterior já pode estar em parte feito; não o repitas.\n' + CONTRACT)


def exhausted_note(hits, repos=()):
    if repos and not [h for h in hits if h not in repos]:
        return '\n\n---\n⚠️ Ficou por fechar no git:\n' + '\n'.join('- ' + r for r in repos)
    return ('\n\n---\n⚠️ Porta de qualidade: após %d ciclos de correção a revisão ainda indicava trabalho em falta. '
            'Estado real acima; o que falta é o que está listado como pendente/must-fix.' % MAX_CYCLES)
