import pathlib
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro as M
import maestro_qagate as G
import maestro_queue as Q

WEB = M.DOMAINS['web']


def classify(text, thread=None, claude=None, codex=None):
    claude = claude or dict(pace_status='ok', five_hour_pct=20, seven_day_pct=10)
    with patch.object(M, 'get_claude_quota', return_value=claude), \
            patch.object(M, 'get_codex_quota', return_value=codex or dict(five_hour_est_pct=20)), \
            patch.object(M, 'get_deepseek_balance', return_value=dict(available=True)), \
            patch.object(M, 'get_antigravity_status', return_value=dict(limited=True)), \
            patch.object(M, 'choose_active_maestro', return_value=dict(engine='codex', name='Codex')):
        return M.classify_task(text, '/tmp', thread=thread)


def thread(level='Média (Execução Cirúrgica)', domain=WEB, engine='sonnet', age=600):
    return M.thread_context([{'role': 'user', 'text': 'pedido'},
                             {'role': 'assistant', 'ts': time.time() - age,
                              'plan': {'dimension': level, 'domain': domain, 'exec_engine': engine}}])


class ThreadClassifierTests(unittest.TestCase):
    """Real follow-ups from the history that were routed to the cheapest model as 'Rápida / Direta'."""

    def test_a_new_conversation_keeps_the_old_behaviour(self):
        plan = classify('que horas são')
        self.assertEqual(plan['dim_level'], 1)
        self.assertEqual(plan['domain_key'], 'nas')

    def test_short_follow_up_continues_the_work_and_its_domain(self):
        plan = classify('Logo bonito e bem feito.', thread())
        self.assertEqual(plan['dim_level'], 2)
        self.assertEqual(plan['domain_key'], 'web')
        self.assertEqual(plan['exec_engine'], 'claude-sonnet')

    def test_saying_it_failed_moves_to_a_stronger_single_engine(self):
        for text in ['Ok conitnua igualzinho no meu. Não sei o que estás a fazer...',
                     'Agora o strategy aparece e desaparece de segundo a segundo, está unitilizavel o maestro',
                     'E AINDA NAO ESTA NO AR PORQUE???', 'Continua bugado']:
            plan = classify(text, thread())
            self.assertEqual(plan['dim_level'], 3, text)
            self.assertEqual(plan['exec_engine'], 'claude-opus', text)
            self.assertFalse(plan['multi_agent'], text)
            self.assertIn('claude-opus', plan['available_engines'])
            # The queue keeps that preference instead of filtering it out.
            engine, _ = Q.decide(dict(id='t', chat_id='c', text=text), plan, [])
            self.assertEqual(engine, 'claude-opus', text)

    def test_after_opus_the_next_attempt_changes_company(self):
        plan = classify('continua igual', thread(engine='opus'))
        self.assertEqual(plan['exec_engine'], 'codex')

    def test_opus_pace_protection_still_wins(self):
        tight = dict(pace_status='ok', five_hour_pct=70, seven_day_pct=10)   # above the Opus 5h guard
        plan = classify('continua igual', thread(), claude=tight)
        self.assertEqual(plan['exec_engine'], 'codex')
        self.assertEqual(plan['dim_level'], 3)

    def test_old_conversations_and_neutral_words_do_not_escalate(self):
        self.assertEqual(classify('continua igual', thread(age=7 * 3600))['dim_level'], 1)
        self.assertEqual(classify('obrigado, ficou bom', thread())['dim_level'], 2)
        self.assertFalse(M.says_it_failed('Continua o trabalho de ontem no site'))
        self.assertEqual(M.thread_context([{'role': 'user', 'text': 'olá'}]), {})


class GateGitWordingTests(unittest.TestCase):
    def test_admitting_no_commit_is_refused_unless_it_was_the_request(self):
        reply = 'Já está em produção. Não fiz commit nem PR, porque não consegui confirmar qual é a pasta git.'
        self.assertTrue(G.check(reply))
        self.assertTrue(G.check('Alterei o CSS; ficou por commitar em ~/DungenLabs_DLX.'))
        self.assertFalse(G.check('Alterei e não fiz commit, como pediste.', 'muda a cor mas não faças commit'))


if __name__ == '__main__':
    unittest.main()
