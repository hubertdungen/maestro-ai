import pathlib
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_web as web

ASKING = 'Fiz o essencial. Pendente: deploy.\nQueres que avance com o deploy?'
DONE = 'Feito e verificado: 12 testes passam, commit abc123 publicado e confirmado com tools/ver.py. Nada pendente.'


class QualityGateTests(unittest.TestCase):
    def gate(self, first, replies, plan=None):
        self.calls = []
        replies = list(replies)

        def run(engine, prompt, text, cwd, images, redo_plan, folder_id=None, job=None):
            self.calls.append(dict(engine=engine, prompt=prompt, plan=redo_plan))
            reply = replies.pop(0)
            return reply if isinstance(reply, dict) else dict(text=reply, rc=0, agent_used='Motor · maker', tools=['t'], plan={})

        job = dict(status='running', t0=time.time(), steps=[])
        with patch.object(web.TURN_QUEUE, 'update') as update:
            result = web.quality_gate(dict(text=first, rc=0, agent_used='Maestro · revisor', tools=[], plan=dict(plan or {})),
                                      'sonnet', 'PROMPT', 'pedido', '/tmp', [], dict(plan or {}), 'nas', job, 'turn', run=run)
        self.updates = update.call_args_list
        return result

    def test_complete_reply_costs_no_extra_run(self):
        self.assertEqual(self.gate(DONE, [])['text'], DONE)
        self.assertEqual(self.calls, [])

    def test_unfinished_reply_is_fixed_inside_the_same_job_without_requeue(self):
        result = self.gate(ASKING, [DONE], plan=dict(workflow={'nodes': []}, read_only=False))
        self.assertEqual(result['text'], DONE)
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertIn('PORTA DE QUALIDADE', call['prompt'])
        self.assertIn('Queres que avance', call['prompt'])        # the refused reply is the work list
        self.assertIn('CONTRATO DE ENTREGA', call['prompt'])
        self.assertFalse(call['plan']['multi_agent'])
        self.assertNotIn('workflow', call['plan'])                 # never reopen the finished plan
        self.assertEqual(result['plan']['workflow'], {'nodes': []})  # the cards stay in the delivered message
        # The turn stays running: it is never sent back to wait in the queue.
        self.assertTrue(all('status' not in c.kwargs for c in self.updates))

    def test_gives_up_after_bounded_cycles_and_says_so(self):
        result = self.gate(ASKING, [ASKING, ASKING, ASKING])
        self.assertEqual(len(self.calls), web.QAGATE.MAX_CYCLES - 1)
        self.assertIn('Porta de qualidade', result['text'])

    def test_failed_fix_keeps_the_last_complete_answer(self):
        result = self.gate(ASKING, [dict(text='erro do fornecedor', rc=1)])
        self.assertIn('Fiz o essencial', result['text'])
        self.assertNotIn('erro do fornecedor', result['text'])


if __name__ == '__main__':
    unittest.main()
