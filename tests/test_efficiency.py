import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_efficiency as E
from maestro_activity import ActivityStream


class EfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / 'efficiency.json'
        self.plan = dict(domain='Web', dim_level=2)

    def train(self, source='coordinator', ok=True, nodes=1):
        return E.evaluate_turn(self.plan, 'a'*400, [dict(supplied_input_estimate=150, usage=None)],
                               dict(status='done' if ok else 'failed', nodes=[{}]*nodes), source, self.path)

    def reason(self, text='Verifica e melhora'):
        return E.local_plan_reason(text, self.plan, ['codex'], self.path)

    def test_cold_start_feedback_and_periodic_probe(self):
        self.assertIsNone(self.reason())
        for _ in range(3): self.train()
        self.assertIsNotNone(self.reason())
        for _ in range(6): self.train(source='local_feedback')
        self.assertIsNone(self.reason())
        self.train()
        self.assertIsNotNone(self.reason())

    def test_failure_complexity_and_explicit_delegation_keep_planner(self):
        for _ in range(3): self.train()
        self.assertIsNone(self.reason('Usa multi-agente e delega as subtarefas'))
        self.plan['dim_level'] = 3
        self.assertIsNone(self.reason())
        self.plan['dim_level'] = 2
        self.train(source='local_feedback', ok=False)
        self.assertIsNone(self.reason())

    def test_multi_node_history_does_not_collapse_work(self):
        for _ in range(3): self.train(nodes=3)
        self.assertIsNone(self.reason())

    def test_unknown_baseline_and_missing_measurements_are_not_savings(self):
        report = self.train()
        self.assertIsNone(report['total_savings_tokens'])
        self.assertIsNone(report['without_maestro_total_tokens'])
        self.assertEqual(report['measured_calls'], 0)
        self.assertEqual(report['input_overhead_estimate'], 50)

    def test_ledger_is_bounded_private_and_read_does_not_rewrite(self):
        for _ in range(E.LIMIT+5):
            E.record_call('SECRET prompt', dict(rc=0, usage=None), {}, 'codex', self.path)
        saved = self.path.read_text()
        self.assertNotIn('SECRET', saved)
        self.assertEqual(len(json.loads(saved)['calls']), E.LIMIT)
        mtime = self.path.stat().st_mtime_ns
        self.assertEqual(E.snapshot(self.path)['calls'], E.LIMIT)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_corrupt_telemetry_keeps_normal_planner(self):
        self.path.write_text('{')
        self.assertIsNone(self.reason())

    def test_resume_without_calls_does_not_train(self):
        E.evaluate_turn(self.plan, 'prompt', [], dict(status='done', nodes=[{}]), 'coordinator', self.path)
        self.assertFalse(self.path.exists())

    def test_cli_measured_usage_and_cache_semantics(self):
        codex = ActivityStream('codex')
        codex.feed(json.dumps(dict(type='turn.completed', usage=dict(input_tokens=100, cached_input_tokens=90, output_tokens=20))))
        self.assertEqual(codex.usage['total_tokens'], 120)
        claude = ActivityStream('claude')
        claude.feed(json.dumps(dict(type='result', usage=dict(input_tokens=10, cache_read_input_tokens=90, cache_creation_input_tokens=5, output_tokens=20))))
        self.assertEqual(claude.usage['total_tokens'], 125)
        self.assertIsNone(E.usage({}, 'codex'))
        self.assertIsNone(ActivityStream('codex').usage)


if __name__ == '__main__': unittest.main()
