import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_qagate as G
import maestro_web as web


def git(root, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
    subprocess.run(['git', '-C', str(root)] + list(args), check=True, capture_output=True, env=env)


class RepoStateTests(unittest.TestCase):
    """The gate reads what the turn left in git, not only what the reply claims."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = pathlib.Path(self.tmp.name)
        self.remote, self.repo = base / 'remote.git', base / 'app'
        subprocess.run(['git', 'init', '-q', '--bare', str(self.remote)], check=True)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        (self.repo / 'old.txt').write_text('v1')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'base')
        git(self.repo, 'remote', 'add', 'origin', str(self.remote))
        git(self.repo, 'push', '-q', '-u', 'origin', 'HEAD')
        # Work in progress that was already there before the turn started.
        (self.repo / 'wip.txt').write_text('do Hubert')
        past = time.time() - 3600
        os.utime(self.repo / 'wip.txt', (past, past))
        self.since = time.time() - 60
        # Never look at the real home folder from a test.
        scan = patch.object(G, '_home_repos', return_value=[])
        scan.start()
        self.addCleanup(scan.stop)

    def test_clean_delivery_and_previous_wip_are_not_blamed(self):
        self.assertEqual(G.repo_findings('Feito em ' + str(self.repo), '/tmp', self.since), [])

    def test_file_changed_in_the_turn_and_left_uncommitted_is_found(self):
        (self.repo / 'novo.py').write_text('x = 1')
        found = G.repo_findings('Alterei `%s/novo.py`.' % self.repo, '/tmp', self.since)
        self.assertEqual(len(found), 1)
        self.assertIn('novo.py', found[0])
        self.assertNotIn('wip.txt', found[0])
        self.assertIn(str(self.repo), G.redo_note('resposta', 1, found))

    def test_commit_made_in_the_turn_and_not_pushed_is_found_from_the_project_folder(self):
        (self.repo / 'old.txt').write_text('v2')
        git(self.repo, 'commit', '-q', '-am', 'feito')
        found = G.repo_findings('Feito e commitado.', str(self.repo), self.since)
        self.assertIn('por enviar', found[0])
        git(self.repo, 'push', '-q')
        self.assertEqual(G.repo_findings('Feito.', str(self.repo), self.since), [])

    def test_repository_named_only_by_a_relative_file_is_found_and_others_are_not_blamed(self):
        (self.repo / 'novo.py').write_text('x = 1')
        (self.repo / 'outro.py').write_text('y = 2')        # someone else's work in the same repository
        with patch.object(G, '_home_repos', return_value=[self.repo]):
            found = G.repo_findings('Ficheiros: `novo.py`, servido do disco.', '/tmp', self.since)
            self.assertEqual(len(found), 1)
            self.assertIn('novo.py', found[0])
            self.assertNotIn('outro.py', found[0])
            self.assertEqual(G.repo_findings('Mudei a cor do botão.', '/tmp', self.since), [])

    def test_request_to_keep_it_local_is_respected(self):
        (self.repo / 'novo.py').write_text('x = 1')
        self.assertEqual(G.repo_findings('ok', str(self.repo), self.since, 'altera isto mas não faças commit'), [])

    def test_gate_sends_the_real_state_back_to_the_maker(self):
        (self.repo / 'novo.py').write_text('x = 1')
        calls = []

        def run(engine, prompt, text, cwd, images, plan, folder_id=None, job=None):
            calls.append(prompt)
            git(self.repo, 'add', '-A')
            git(self.repo, 'commit', '-q', '-m', 'fecho')
            git(self.repo, 'push', '-q')
            return dict(text='Feito e verificado: commit publicado.', rc=0, agent_used='Motor', tools=[], plan={})

        job = dict(status='running', t0=self.since, steps=[])
        with patch.object(web.TURN_QUEUE, 'update'):
            result = web.quality_gate(dict(text='Feito e verificado, tudo publicado.', rc=0, tools=[], plan={}), 'sonnet', 'PROMPT',
                                      'adiciona o novo.py', str(self.repo), [], {}, 'nas', job, 'turn', run=run)
        self.assertEqual(len(calls), 1)                       # the reply looked finished; the repository said otherwise
        self.assertIn('novo.py', calls[0])
        self.assertEqual(result['text'], 'Feito e verificado: commit publicado.')


class GateWordingTests(unittest.TestCase):
    def test_cache_excuses_and_unseen_pages_are_refused(self):
        for text in ['Publicado. Faz Ctrl+F5 se continuar igual.', 'Está no ar; carrega em Ctrl+Shift+R.',
                     'Alterei o CSS. Não vi a página num browser, só confirmei o HTML.']:
            self.assertTrue(G.check(text), text)
        self.assertFalse(G.check('Feito e verificado: a captura do tools/ver.py mostra o cartão novo no telemóvel.'))


class SnapshotTests(unittest.TestCase):
    def test_poll_receives_the_conversation_only_when_it_changed(self):
        chat = {'id': 'c1', 'updated_at': 5.0, 'messages': [{'role': 'user', 'text': 'olá'}]}
        with patch.object(web, 'load_chat', side_effect=lambda cid: dict(chat, messages=list(chat['messages']))), \
                patch.object(web, 'get_job_snapshot', return_value=None):
            first = web.chat_activity_snapshot('c1')
            version = first['chat']['_v']
            again = web.chat_activity_snapshot('c1', version)
            self.assertIsNone(again['chat'])
            self.assertTrue(again['chat_same'])
            chat['messages'].append({'role': 'assistant', 'text': 'feito'})
            chat['updated_at'] = 6.0
            fresh = web.chat_activity_snapshot('c1', version)
            self.assertEqual(len(fresh['chat']['messages']), 2)
            self.assertFalse(fresh['chat_same'])

    def test_claude_sign_in_warning_only_when_it_is_close(self):
        import maestro as M
        now = 1_800_000_000
        iso = lambda days: __import__('datetime').datetime.fromtimestamp(now + days * 86400, __import__('datetime').timezone.utc).isoformat().replace('+00:00', 'Z')
        self.assertEqual(M.claude_login_warning(now, {'loggedIn': True, 'loginExpiresAt': iso(20)}), '')
        self.assertIn('faltam 5 dias', M.claude_login_warning(now, {'loggedIn': True, 'loginExpiresAt': iso(5)}))
        self.assertIn('sem sessão', M.claude_login_warning(now, {'loggedIn': False}))
        self.assertEqual(M.claude_login_warning(now, {}), '')

if __name__ == '__main__':
    unittest.main()
