import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_workflow as WF
import maestro_model_policy as P


def node(nid, **kw):
    return dict(dict(id=nid, title=nid, instruction='faz', engine='opus', read_only=True, depends_on=[]), **kw)


class PlannerRulesTests(unittest.TestCase):
    def test_model_id_written_as_engine_is_accepted(self):
        plan = WF.validate_plan({'nodes': [node('scout', engine='haiku:haiku', role='scout')]}, ['haiku', 'opus'])
        self.assertEqual(plan['nodes'][0]['engine'], 'haiku')
        self.assertEqual(plan['nodes'][0]['model_id'], 'haiku:haiku')

    def test_architecture_analysis_gets_high_effort_role(self):
        plan = WF.validate_plan({'nodes': [node('arquitetura', role='analysis')]}, ['opus'])
        self.assertEqual(plan['nodes'][0]['role'], 'architecture')
        self.assertEqual(P.effort_for({'efforts': ['low', 'medium', 'high']}, 'architecture'), 'high')
        with self.assertRaises(ValueError):
            WF.validate_plan({'nodes': [node('arq', role='architecture', read_only=False)]}, ['opus'])

    def test_guidance_matches_request_type(self):
        g = P.guidance('Quero uma galeria React com mockups, nada profissional')
        self.assertIn('VISUAL', g); self.assertIn('IMAGENS', g); self.assertIn('INSATISFAÇÃO', g)
        self.assertEqual(P.guidance('reinicia o qbittorrent'), '')
        self.assertIn('TABELA DE PREFERÊNCIAS', WF.planner_prompt('galeria', ['opus']))


if __name__ == '__main__':
    unittest.main()
