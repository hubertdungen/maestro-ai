import unittest

import maestro_web as W


class SupersededProposalTests(unittest.TestCase):
    chat = dict(id='c1', messages=[
        dict(role='user', text='P2B', turn_id='t1'),
        dict(role='assistant', text='feito; falta X', turn_id='t1'),
    ])
    marker = '[apply-proposal-t1]\nContinua…'

    def test_last_reply_with_idle_chat_is_allowed(self):
        self.assertIsNone(W.superseded_proposal(self.chat, self.marker, []))
        self.assertIsNone(W.superseded_proposal(self.chat, 'pedido normal', []))

    def test_later_order_refuses_continue(self):
        chat = dict(self.chat, messages=self.chat['messages'] + [dict(role='user', text='P2C1', turn_id='t2')])
        self.assertIn('posteriores', W.superseded_proposal(chat, self.marker, []))

    def test_running_turn_in_chat_refuses_continue(self):
        tasks = [dict(chat_id='c1', status='running', text='P2C1')]
        self.assertIn('fila', W.superseded_proposal(self.chat, self.marker, tasks))
        # Turnos de outras conversas, ou o próprio pedido do botão, não contam.
        self.assertIsNone(W.superseded_proposal(self.chat, self.marker, [dict(chat_id='c2', status='running', text='x')]))
        self.assertIsNone(W.superseded_proposal(self.chat, self.marker, [dict(chat_id='c1', status='queued', text=self.marker)]))

    def test_own_request_message_does_not_count_as_later_work(self):
        chat = dict(self.chat, messages=self.chat['messages'] + [dict(role='user', text=self.marker, turn_id='t3')])
        self.assertIsNone(W.superseded_proposal(chat, self.marker, []))


if __name__ == '__main__':
    unittest.main()
