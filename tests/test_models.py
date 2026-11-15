import pathlib
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_models as M


class ModelTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(M, '_deepseek_api_models', return_value=[])
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(M, '_sandbox_readonly_available', return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def sample(self):
        return {'models': [M._entry('codex', 'test-model', 'Test', ['low', 'high'], 'low', 'fixture'),
                           M._entry('agy', 'listed-high', 'AGY', [], None, 'fixture')], 'notes': [], 'fetched_at': 0}

    def test_exact_selection_and_effort(self):
        with patch.object(M, 'catalog', return_value=self.sample()):
            self.assertEqual(M.resolve('codex:test-model', 'high')['effort'], 'high')
            for mid, effort in [('codex:invented', None), ('codex:test-model', 'ultra')]:
                with self.assertRaises(ValueError):
                    M.resolve(mid, effort)

    def test_quota_is_separate_from_catalogue_and_does_not_mutate_cache(self):
        with patch.object(M, '_CACHE', self.sample()), patch.object(M, '_CACHE_AT', M.time.time()):
            blocked = M.catalog({'codex': {'seven_day_pct': 100}})
            self.assertFalse(blocked['models'][0]['available'])
            self.assertTrue(M.catalog()['models'][0]['available'])

    def test_readonly_command_and_unconfined_refusal(self):
        with patch.object(M, 'catalog', return_value=self.sample()):
            args = M.command_config({'id': 'codex:test-model', 'effort': 'high'}, read_only=True)
            self.assertIn('read-only', args)
            self.assertIn('model_reasoning_effort="high"', args)
            with self.assertRaises(ValueError):
                M.command_config({'id': 'agy:listed-high'}, read_only=True)

    def test_no_invented_models_when_discovery_fails(self):
        with patch.object(M.shutil, 'which', return_value='/fake'), patch.object(M, '_run', return_value=''), patch.object(M.pathlib.Path, 'read_text', side_effect=OSError):
            data = M._discover()
            self.assertFalse(any(e['engine'] in ('codex', 'agy') for e in data['models']))
            self.assertTrue(data['notes'])

    def test_agy_exact_ids_only(self):
        def run(args):
            return 'Fetching available models...\ngemini-4-argon-high\tGemini 4 Argon (High)\ninvalid id\tNope' if pathlib.Path(args[0]).name == 'agy' else ''
        with patch.object(M, '_binary', side_effect=lambda n: '/agy' if n == 'agy' else ''), patch.object(M, '_run', side_effect=run):
            data = M._discover()
            self.assertEqual([e['id'] for e in data['models']], ['agy:gemini-4-argon-high'])

if __name__ == '__main__':
    unittest.main()
