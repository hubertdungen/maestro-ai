"""Quality gate: a reply that says "not ready / must-fix / pending / tell me where" is not delivered; it goes back to the dev loop."""
import pathlib
import re

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
_OK = re.compile(r'(?:nada|nenhum[ao]?|sem|zero|0)\s+(?:\w+\s+)?(?:pendente|must-?fix|lacuna|bloqueio)\w*', re.I)


def check(text):
    """Return a list of matched reasons (empty = acceptable to deliver)."""
    t = _OK.sub('', str(text or ''))
    hits = [p for p in _NEG if re.search(p, t, re.I | re.M)]
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


def redo_note(text, cycle):
    body = str(text or '').strip()[-3500:]
    return ('\n\n[PORTA DE QUALIDADE · CICLO %d/%d · a entrega anterior foi RECUSADA pelo Maestro]\n' % (cycle, MAX_CYCLES) +
            'A revisão/resposta anterior declarou o trabalho incompleto:\n---\n' + body + '\n---\n'
            'Isto não é um relatório para o Hubert: é a tua lista de trabalho. Implementa TODOS os must-fix, lacunas funcionais e melhorias razoáveis, '
            'valida (testes/execução real), e só então responde. Não divides em fases, não termines com perguntas de permissão; '
            'se faltar um dado (ex.: onde fazer o deploy), assume o default razoável, executa e diz qual. '
            'Verifica primeiro o estado real (git status, ficheiros, serviço): o trabalho anterior já pode estar em parte feito; não o repitas.\n' + CONTRACT)


def exhausted_note(hits):
    return ('\n\n---\n⚠️ Porta de qualidade: após %d ciclos de correção a revisão ainda indicava trabalho em falta. '
            'Estado real acima; o que falta é o que está listado como pendente/must-fix.' % MAX_CYCLES)
