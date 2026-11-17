"""Exercise restricted inspections against a real repository, without inference."""
import pathlib
import subprocess
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'tools' / 'inspect_repo.py'


class RepoInspectionTests(unittest.TestCase):
    def test_inspects_repo_with_spaces_without_running_local_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / 'repo with spaces'
            repo.mkdir()
            def git(*args):
                return subprocess.run(['git', '-C', str(repo), *args], check=True,
                                      capture_output=True, text=True)
            git('init', '-q')
            git('config', 'user.email', 'test@example.invalid')
            git('config', 'user.name', 'Test')
            source = repo / 'file.txt'
            source.write_text('original\n')
            git('add', '.')
            git('commit', '-qm', 'Initial fixture')
            source.write_text('changed\n')
            sentinel = pathlib.Path(tmp) / 'hook-ran'
            hook = pathlib.Path(tmp) / 'monitor'
            hook.write_text('#!/bin/sh\ntouch "' + str(sentinel) + '"\n')
            hook.chmod(0o700)
            git('config', 'core.fsmonitor', str(hook))
            index = (repo / '.git' / 'index').read_bytes()
            expected = {'status': ' M file.txt', 'log': 'Initial fixture',
                        'diff': '+changed', 'staged': '', 'branches': '* '}
            for action, snippet in expected.items():
                with self.subTest(action=action):
                    result = subprocess.run(['python3', str(SCRIPT), action, str(repo)],
                                            capture_output=True, text=True, timeout=35)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(snippet, result.stdout)
            self.assertFalse(sentinel.exists())
            self.assertEqual((repo / '.git' / 'index').read_bytes(), index)
            self.assertEqual(source.read_text(), 'changed\n')

    def test_rejects_mutations_and_extra_git_flags(self):
        for args in [('fetch', '/tmp'), ('status', '/tmp', '--output=/tmp/nope')]:
            result = subprocess.run(['python3', str(SCRIPT), *args], capture_output=True)
            self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
