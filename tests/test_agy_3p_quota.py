import datetime as dt
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import maestro as M
import maestro_models as MODELS


def _log(model):
    t = dt.datetime.now(M.TZ)
    stamp = t.strftime('%m%d %H:%M:%S.000000')
    return (f"I{stamp}       1 model_resolver.go:107] Resolving model {model}\n"
            f"E{stamp}     724 errorreport.go:285] agent executor error: generating and executing: "
            "RESOURCE_EXHAUSTED (code 429): Individual quota reached. Please upgrade your subscription "
            "to increase your limits. Resets in 157h27m27s.\n"), t.strftime('cli-%Y%m%d_%H%M%S.log')


class Agy3pQuotaTests(unittest.TestCase):
    def status(self, model):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'log').mkdir()
            text, name = _log(model)
            (d / 'log' / name).write_text(text)
            # Quota OAuth em cache: Gemini livre (não vai à rede).
            (d / 'agy-quota-cache.json').write_text(json.dumps({'ts': time.time(), 'data': {
                'models': {'gemini-3-flash': {'quotaInfo': {'remainingFraction': 0.99}}}, 'summary': {}}}))
            M._AGY_SIGNAL_MEM.update(key=None, value=None, ts=0.0)
            with patch.object(M, 'STATE_DIR', d), patch.object(M, 'AGY_LOG_DIR', d / 'log'), \
                 patch.object(M, 'AGY_COOLDOWN_FILE', d / 'cooldown.json'), \
                 patch.object(M, 'AGY_QUOTA_SIGNAL_FILE', d / 'signal.json'):
                return M.get_antigravity_status()

    def test_429_on_claude_model_blocks_only_3p_models(self):
        s = self.status('claude-opus-5-5-high')
        self.assertFalse(s['limited'])
        self.assertTrue(s['models_3p_limited'])
        self.assertLess(s['gemini_used_pct'], 100)
        self.assertEqual(s['models_3p_7d_used_pct'], 100.0)

    def test_429_on_gemini_model_still_blocks_antigravity(self):
        s = self.status('gemini-4-argon-high')
        self.assertTrue(s['limited'])
        self.assertFalse(s['models_3p_limited'])

    def test_catalog_hides_only_claude_and_gpt_under_antigravity(self):
        cache = dict(models=[dict(id=i, engine='agy', note='') for i in
                             ('agy:claude-opus-5-5-high', 'agy:gpt-oss-120b-medium', 'agy:gemini-4-argon-high')])
        with patch.object(MODELS, '_CACHE', cache), patch.object(MODELS, '_CACHE_AT', time.time()):
            got = {e['id']: e.get('available', True) for e in
                   MODELS.catalog({'antigravity': {'limited': False, 'models_3p_limited': True}})['models']}
        self.assertEqual(got, {'agy:claude-opus-5-5-high': False, 'agy:gpt-oss-120b-medium': False,
                               'agy:gemini-4-argon-high': True})


if __name__ == '__main__':
    unittest.main()
