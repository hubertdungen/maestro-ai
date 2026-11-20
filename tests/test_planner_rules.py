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
        self.assertIn('ssh desktop', P.guidance('cria um projeto no Unreal Engine 5'))
        self.assertIn('Unreal Engine / PC Windows', WF.planner_prompt('ue5', ['opus']))
        self.assertIn('TABELA DE PREFERÊNCIAS', WF.planner_prompt('galeria', ['opus']))


if __name__ == '__main__':
    unittest.main()

class UnrealEngineRuleTests(unittest.TestCase):
    def test_ue_write_step_leaves_incapable_engine(self):
        for weak in ('haiku', 'agy', 'deepseek'):
            plan = WF.validate_plan({'nodes': [node('ue', engine=weak, read_only=False, role='implementation',
                                                    instruction='Cria o projeto no Unreal por ssh desktop', model_id=weak + ':x')]},
                                    [weak, 'opus', 'codex'])
            self.assertEqual(plan['nodes'][0]['engine'], 'codex')
            self.assertNotIn('model_id', plan['nodes'][0])

    def test_ue_capable_engine_and_read_only_steps_are_kept(self):
        plan = WF.validate_plan({'nodes': [
            node('a', engine='fable', read_only=False, role='implementation', instruction='mexe no UE5'),
            node('b', engine='haiku', role='scout', instruction='lê as capturas do Unreal no NAS')]}, ['fable', 'haiku'])
        self.assertEqual([n['engine'] for n in plan['nodes']], ['fable', 'haiku'])

    def test_ue_step_without_capable_engine_is_refused(self):
        with self.assertRaises(ValueError):
            WF.validate_plan({'nodes': [node('ue', engine='haiku', read_only=False, role='implementation',
                                               instruction='build com RunUAT')]}, ['haiku', 'agy'])

    def test_fallback_plan_and_non_ue_steps(self):
        plan = WF.default_plan('faz a fase 1 em Unreal Engine 5', ['agy', 'haiku', 'opus'], preferred='agy')
        self.assertEqual(next(n for n in plan['nodes'] if n['id'] == 'implement')['engine'], 'opus')
        plan = WF.validate_plan({'nodes': [node('x', engine='haiku', read_only=False, role='implementation')]}, ['haiku', 'opus'])
        self.assertEqual(plan['nodes'][0]['engine'], 'haiku')

    def test_ue_read_step_leaves_engines_without_tools(self):
        plan = WF.validate_plan({'nodes': [node('scout', engine='deepseek', role='scout', instruction='inventário do projeto Unreal'),
                                           node('s2', engine='agy', role='scout', instruction='lê o VISAO do UE5')]},
                                ['deepseek', 'agy', 'haiku', 'opus'])
        self.assertEqual([n['engine'] for n in plan['nodes']], ['haiku', 'haiku'])
        plan = WF.validate_plan({'nodes': [node('scout', engine='deepseek', role='scout', instruction='inventário do Unreal')]}, ['deepseek'])
        self.assertEqual(plan['nodes'][0]['engine'], 'deepseek')  # sem alternativa, não bloqueia a leitura

    def test_fallback_plan_scout_for_ue_has_tools(self):
        plan = WF.default_plan('PARIDADE P1 no Unreal Engine 5', ['deepseek', 'haiku', 'opus', 'sonnet'], preferred='opus')
        self.assertEqual(next(n for n in plan['nodes'] if n['id'] == 'scout')['engine'], 'haiku')

