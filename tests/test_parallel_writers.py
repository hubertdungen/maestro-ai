import pathlib
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import maestro_workflow as WF
import maestro_worktree as WT


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *WT.IDENT, *args], check=True, capture_output=True, text=True).stdout.strip()


class Repo:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q', '-b', 'main')
        (self.repo / 'docs').mkdir()
        (self.repo / 'src').mkdir()
        (self.repo / 'docs' / 'a.md').write_text('a\n')
        (self.repo / 'src' / 'x.cpp').write_text('x\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'base')
        self.patch = patch.object(WT, 'ROOT', self.root / 'wt')
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()
        self.tmp.cleanup()


def node(nid, read_only=False, **extra):
    return dict(id=nid, title=nid, instruction='faz', engine='codex', read_only=read_only,
                role='analysis' if read_only else 'implementation', depends_on=[], **extra)


class SchedulingTests(unittest.TestCase):
    def test_validation_keeps_valid_scope_and_drops_bad_one(self):
        with Repo() as r:
            plan = WF.validate_plan({'nodes': [
                node('docs', write_paths=['docs/'], repo=str(r.repo)),
                node('mau', write_paths=['../fora'], repo=str(r.repo)),
                node('sem_repo', write_paths=['src'], repo='/nao/existe'),
            ]}, ['codex'])
        docs, bad, norepo = plan['nodes']
        self.assertTrue(WF.isolated(docs))
        self.assertFalse(WF.isolated(bad))
        self.assertFalse(WF.isolated(norepo))

    def test_can_start_rules(self):
        a = node('a', write_paths=['docs'], repo='/r')
        b = node('b', write_paths=['src'], repo='/r')
        c = node('c', write_paths=['docs/VISAO.md'], repo='/r')
        plain, reader = node('plain'), node('ler', read_only=True)
        self.assertTrue(WF.can_start(b, [a]))          # âmbitos disjuntos
        self.assertFalse(WF.can_start(c, [a]))         # docs/VISAO.md está dentro de docs
        self.assertFalse(WF.can_start(plain, [a]))     # escritor sem âmbito corre sozinho
        self.assertFalse(WF.can_start(a, [plain]))
        self.assertTrue(WF.can_start(reader, [a]))     # leitores com escritores isolados
        self.assertFalse(WF.can_start(reader, [plain]))
        self.assertTrue(WF.can_start(a, [reader]))

    def test_isolated_writers_run_at_the_same_time_and_plain_writer_waits(self):
        with tempfile.TemporaryDirectory() as d:
            nodes = [node('a', write_paths=['docs'], repo='/r'), node('b', write_paths=['src'], repo='/r'),
                     dict(node('pub'), depends_on=[])]
            wf = WF.Workflow(pathlib.Path(d) / 'wf.json', {'nodes': nodes}, objective='x')
            live, peak, lock = set(), [], threading.Lock()

            def execute(n, ctx):
                with lock:
                    live.add(n['id']); peak.append(set(live))
                time.sleep(0.3)
                with lock:
                    live.discard(n['id'])
                return {'text': 'ok'}
            final = wf.run(execute)
        self.assertEqual(final['status'], 'done')
        self.assertIn({'a', 'b'}, peak)
        self.assertFalse(any('pub' in s and len(s) > 1 for s in peak))


class WorktreeTests(unittest.TestCase):
    def test_changes_in_scope_are_committed_and_merged(self):
        with Repo() as r:
            wt = WT.open_worktree(r.repo, 't1-docs')
            pathlib.Path(wt['path'], 'docs', 'VISAO.md').write_text('visão\n')
            ok, note = WT.integrate(r.repo, wt, ['docs'], 'Visão')
            self.assertTrue(ok, note)
            self.assertEqual((r.repo / 'docs' / 'VISAO.md').read_text(), 'visão\n')
            self.assertFalse(pathlib.Path(wt['path']).exists())
            self.assertEqual(git(r.repo, 'branch', '--list', wt['branch']), '')

    def test_out_of_scope_change_without_conflict_is_integrated_with_note(self):
        with Repo() as r:
            wt = WT.open_worktree(r.repo, 't1-docs')
            pathlib.Path(wt['path'], 'src', 'x.cpp').write_text('mudei\n')
            ok, note = WT.integrate(r.repo, wt, ['docs'], 'Visão')
            self.assertTrue(ok, note)
            self.assertIn('src/x.cpp', note)
            self.assertIn('Fora do âmbito', note)
            self.assertEqual((r.repo / 'src' / 'x.cpp').read_text(), 'mudei\n')

    def test_conflict_aborts_merge_and_keeps_branch(self):
        with Repo() as r:
            wt = WT.open_worktree(r.repo, 't1-docs')
            pathlib.Path(wt['path'], 'docs', 'a.md').write_text('do agente\n')
            (r.repo / 'docs' / 'a.md').write_text('no principal\n')
            git(r.repo, 'commit', '-qam', 'concorrente')
            ok, note = WT.integrate(r.repo, wt, ['docs'], 'Visão')
            self.assertFalse(ok)
            self.assertIn('Conflito', note)
            self.assertEqual((r.repo / 'docs' / 'a.md').read_text(), 'no principal\n')
            self.assertEqual(git(r.repo, 'status', '--porcelain'), '')
            self.assertTrue(git(r.repo, 'branch', '--list', wt['branch']))


    def test_git_sees_user_bin_for_lfs(self):
        # git-lfs vive em ~/.local/bin, fora do PATH do serviço.
        self.assertTrue(WT.ENV['PATH'].startswith(str(pathlib.Path.home() / '.local' / 'bin')))


class RunningCommandTests(unittest.TestCase):
    def test_shows_descendant_command_without_shells(self):
        import maestro_web as W
        p = subprocess.Popen(['bash', '-c', 'sleep 7; true'])
        try:
            time.sleep(0.3)
            self.assertEqual(W.running_command(p.pid), 'sleep 7')
        finally:
            p.kill(); p.wait()


if __name__ == '__main__':
    unittest.main()
