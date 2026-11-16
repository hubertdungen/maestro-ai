import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_web as web


class ConversationHistoryTests(unittest.TestCase):
    def prompt(self, messages, text='parcerias softwares', chat_id='chat-history'):
        plan = {'domain': 'NAS', 'dimension': 'Média', 'agents': ['codex'],
                'turn_id': 'active', 'quotas': {
                    'claude': {'seven_day_pct': 30, 'pace_target_pct': 38},
                    'codex': {'five_hour_est_pct': 0},
                    'deepseek': {'balance_text': 'test'}}}
        with patch.object(web, 'get_folder_map', return_value={}):
            return web.build_conversation_prompt({'id': chat_id, 'messages': messages}, text, [], plan)

    def test_original_request_and_old_answer_survive_recent_window(self):
        messages = [{'role': 'user', 'text': 'Original: HTA e Z89. Ferramentas SOLIDWORKS e Fusion.'},
                    {'role': 'user', 'text': 'Parcerias reais; softwares apenas como texto.'}]
        messages += [{'role': 'assistant', 'text': f'Recent update {i}'} for i in range(15)]
        result = self.prompt(messages)
        self.assertIn('Original: HTA e Z89', result)
        self.assertIn('Parcerias reais; softwares apenas como texto.', result)
        self.assertIn(str(web.CHATS_DIR / 'chat-history.json'), result)
        self.assertIn('consulta este ficheiro', result)

    def test_excludes_inflight_and_current_turns(self):
        result = self.prompt([
            {'role': 'user', 'turn_id': 'other-pending', 'text': 'PRIVATE PENDING'},
            {'role': 'user', 'turn_id': 'active', 'text': 'CURRENT DUPLICATE'},
            {'role': 'user', 'turn_id': 'done', 'text': 'Completed requirement'},
            {'role': 'assistant', 'turn_id': 'done', 'text': 'Completed response'}])
        self.assertNotIn('PRIVATE PENDING', result)
        self.assertNotIn('CURRENT DUPLICATE', result)
        self.assertIn('Completed requirement', result)

    def test_continuity_rule_survives_stale_handoff_and_history_truncation(self):
        messages = [{'role': 'assistant', 'text': 'Só leitura; aguardo OK para merge.'}]
        messages += [{'role': 'assistant', 'text': 'Old update ' * 300} for _ in range(15)]
        result = self.prompt(messages, text='Conclui o pedido anterior.')
        rule = result.index('[REGRA PERMANENTE DO MAESTRO')
        self.assertGreater(rule, result.index('=== FIM DO HISTÓRICO ==='))
        self.assertLess(rule, result.index('MENSAGEM ATUAL DO HUBERT:'))
        self.assertIn('recupera o pedido e as decisões no histórico completo', result)
        self.assertIn('não revogam autorizações posteriores', result)
        self.assertIn('confirma que a alteração concreta aparece em produção', result)
        self.assertIn('bloqueio real', result)

    def test_every_earlier_request_stays_in_the_ledger_and_replies_keep_their_ending(self):
        messages = [{'role': 'user', 'text': f'pedido antigo número {i} sobre o tema {i}'} for i in range(25)]
        messages += [{'role': 'assistant', 'text': 'Início. ' + 'meio ' * 800 + 'Ficou por publicar o PR 7.'}]
        result = self.prompt(messages, text='continua')
        for i in range(16):
            self.assertIn(f'pedido antigo número {i} ', result)
        self.assertIn('Ficou por publicar o PR 7.', result)
        self.assertIn('contrato de entrega'.upper(), result)

    def test_older_context_is_bounded_and_chat_path_sanitized(self):
        messages = [{'role': 'user', 'text': 'parcerias ' + 'x' * 50000} for _ in range(30)]
        result = self.prompt(messages, chat_id='../chat-safe')
        older = result.split('=== PEDIDOS E RESPOSTAS ANTERIORES')[1].split('=== FIM DOS PEDIDOS ANTERIORES')[0]
        self.assertLess(len(older), 14500)
        self.assertIn(str(web.CHATS_DIR / 'chat-safe.json'), result)
        self.assertNotIn('../chat-safe', result)


if __name__ == '__main__':
    unittest.main()
