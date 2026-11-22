import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

import maestro_worktree as WT


class ScopeTest(unittest.TestCase):
    def test_patterns_match_at_any_depth(self):
        scope = ['FragorHumanoid*', 'Tests/Corpo', 'Source/Fragor/Private/Humanoid*']
        self.assertTrue(WT.inside('Source/Fragor/Private/FragorHumanoid.cpp', scope))
        self.assertTrue(WT.inside('Source/Fragor/Public/FragorHumanoid.h', scope))
        self.assertTrue(WT.inside('Source/Fragor/Private/Tests/Corpo/FragorCorpoTests.cpp', scope))
        self.assertTrue(WT.inside('Source/Fragor/Private/HumanoidX.cpp', scope))
        self.assertFalse(WT.inside('Source/Fragor/Private/Ensaio/FragorEnsaio.cpp', scope))
        self.assertTrue(WT.inside('docs/a.md', ['docs/']))
        self.assertTrue(WT.inside('web/js/a.js', ['web/**']))


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.repo = root / 'repo'
        self.repo.mkdir()
        git = lambda *a: subprocess.run(['git', '-C', str(self.repo), *WT.IDENT, *a], check=True, capture_output=True)
        git('init', '-q', '-b', 'main')
        (self.repo / 'a.txt').write_text('a\n')
        (self.repo / 'b.txt').write_text('b\n')
        git('add', '-A'); git('commit', '-q', '-m', 'base')
        self.patch = mock.patch.object(WT, 'ROOT', root / 'wts')
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_failed_attempt_is_kept_and_out_of_scope_merges_clean(self):
        wt = WT.open_worktree(self.repo, 't1-corpo')
        self.assertFalse(wt['resumed'])
        pathlib.Path(wt['path'], 'a.txt').write_text('a1\n')
        subprocess.run(['git', '-C', wt['path'], *WT.IDENT, 'commit', '-qam', 'wip'], check=True)
        again = WT.open_worktree(self.repo, 't1-corpo')
        self.assertTrue(again['resumed'])
        self.assertEqual(pathlib.Path(again['path'], 'a.txt').read_text(), 'a1\n')
        pathlib.Path(again['path'], 'b.txt').write_text('b1\n')
        ok, note = WT.integrate(self.repo, again, ['a.txt'], 'corpo')
        self.assertTrue(ok, note)
        self.assertIn('Fora do âmbito', note)
        self.assertEqual((self.repo / 'b.txt').read_text(), 'b1\n')


if __name__ == '__main__':
    unittest.main()
