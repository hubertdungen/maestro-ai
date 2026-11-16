import importlib.util
import json
import pathlib
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import maestro as M
import maestro_qagate as G

spec = importlib.util.spec_from_file_location('saude', ROOT / 'tools' / 'saude.py')
saude = importlib.util.module_from_spec(spec)
spec.loader.exec_module(saude)


class HealthTests(unittest.TestCase):
    def test_indicators_come_from_the_recorded_conversations(self):
        chats = [{'messages': [
            {'role': 'user', 'ts': 10, 'text': 'muda a cor'},
            {'role': 'assistant', 'ts': 20, 'text': 'Feito. Queres que faça o deploy?'},
            {'role': 'user', 'ts': 30, 'text': 'continua igual, não funciona'},
            {'role': 'assistant', 'ts': 40, 'text': 'Feito e verificado: commit abc publicado.'},
            {'role': 'user', 'ts': 45, 'text': '[apply-proposal-x]\ncontinua'},
            {'role': 'assistant', 'ts': 500, 'text': 'fora do período'}]}]
        queue = [{'ts': 10, 'started_at': 14, 'qa_cycles': 1, 'qa_reasons': [['acaba a pedir autorização']]},
                 {'ts': 30, 'started_at': 90}]
        history = [{'ts': 20, 'worker': 'Motor · Claude Sonnet', 'rc': 0}, {'ts': 40, 'worker': 'Motor · DeepSeek (x)', 'rc': 1}]
        m = saude.measure(chats, queue, history, 0, 100)
        self.assertEqual((m['pedidos teus'], m['respostas'], m['cliques em botões «continuar/aplicar»']), (2, 2, 1))
        self.assertEqual(m['respostas que acabam a pedir autorização (%)'], 50)
        self.assertEqual(m['pedidos teus a dizer que falhou (%)'], 50)
        self.assertEqual(m['turnos corrigidos pela porta (%)'], 50)
        self.assertEqual(m['espera na fila, mediana (s)'], 32)
        self.assertEqual(m['_motivos da porta'], {'acaba a pedir autorização': 1})
        self.assertEqual(m['_falhas'], {'DeepSeek': 1})

    def test_gate_reasons_are_readable(self):
        hits = G.check('Fiz o essencial. Não fiz commit. Queres que avance?') + ['/repo: 1 ficheiro(s) por commitar (a.py)']
        self.assertEqual(sorted(G.labels(hits)), sorted(['acaba a pedir autorização', 'diz que não fechou o git',
                                                         'git: /repo: 1 ficheiro(s) por commitar (a.py)']))

    def test_state_files_survive_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'state.json'

            def write(n):
                for i in range(60):
                    M._save_json(path, {'writer': n, 'payload': 'x' * (50 + (i * n) % 400)})
            workers = [threading.Thread(target=write, args=(n,)) for n in range(1, 5)]
            for w in workers:
                w.start()
            for _ in range(200):
                if path.exists():
                    json.loads(path.read_text())   # never a half-written file
            for w in workers:
                w.join()
            self.assertIn('writer', json.loads(path.read_text()))


if __name__ == '__main__':
    unittest.main()
