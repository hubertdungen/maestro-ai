import copy
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_web as web
import maestro_organizer as organizer


class ChatOrganizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = pathlib.Path(self.temp.name)
        self.chats = self.home / 'chats'
        self.chats.mkdir()
        (self.home / 'scripts' / 'maestro').mkdir(parents=True)
        self.folders = copy.deepcopy(web.F.DEFAULT_FOLDERS)
        for name, value in [('HOME', self.home), ('CHATS_DIR', self.chats), ('ACTIVE_JOBS', {})]:
            p = patch.object(web, name, value); p.start(); self.addCleanup(p.stop)
        for name in ['ensure_all_dirs']:
            p = patch.object(web, name); p.start(); self.addCleanup(p.stop)
        p = patch.object(web.F, 'get_folders', side_effect=lambda: copy.deepcopy(self.folders))
        p.start(); self.addCleanup(p.stop)
        def save_folder(folder):
            self.folders.append({k:v for k,v in folder.items() if k != 'aliases'})
            return self.folders
        p = patch.object(web.F, 'save_folder', side_effect=save_folder)
        p.start(); self.addCleanup(p.stop)

    def chat(self, cid, text, idle=2701, **extra):
        data = dict(id=cid, title=text, project='nas', updated_at=10000-idle,
                    messages=[dict(role='user', text=text, ts=10000-idle)], **extra)
        (self.chats / (cid+'.json')).write_text(json.dumps(data))
        return data

    def read(self, cid):
        return json.loads((self.chats / (cid+'.json')).read_text())

    def test_idle_and_running_guards(self):
        self.chat('recent', 'Maestro', idle=2699)
        self.chat('running', 'Maestro', idle=5000)
        web.ACTIVE_JOBS['running'] = {'status':'running'}
        self.chat('boundary', 'Maestro', idle=2700)
        changes = web.organize_idle_chats(10000)
        self.assertEqual([c['id'] for c in changes], ['boundary'])
        self.assertNotIn('folder_id', self.read('recent'))
        self.assertNotIn('folder_id', self.read('running'))

    def test_filing_preserves_conversation_execution_and_order(self):
        before = self.chat('media', 'sacar filmes no Radarr')
        web.organize_idle_chats(10000)
        after = self.read('media')
        self.assertEqual(after['folder_id'], 'media')
        for key, value in before.items():
            self.assertEqual(after[key], value)
        self.assertEqual(web.organize_idle_chats(10001), [])
        stale = copy.deepcopy(before)
        web.save_chat(stale)
        self.assertEqual(self.read('media')['folder_id'], 'media')

    def test_discovers_new_project_and_deduplicates(self):
        project = self.home / 'NewSector'
        project.mkdir(); (project / 'package.json').write_text('{}')
        (self.home / 'Downloads').mkdir()
        self.chat('project', 'Corrigir NewSector')
        self.chat('project2', 'NewSector API')
        web.organize_idle_chats(10000)
        self.assertEqual(self.read('project')['folder_id'], self.read('project2')['folder_id'])
        self.assertEqual(len([f for f in self.folders if f['name']=='NewSector']), 1)
        self.assertFalse(any(f['name']=='Downloads' for f in self.folders))

    def test_manual_ambiguous_empty_and_assistant_mentions(self):
        self.chat('manual', 'Maestro', folder_id='web', folder_source='manual')
        self.chat('ambiguous', 'Maestro e Radarr')
        data = self.chat('assistant', 'ajuda-me')
        data['messages'].append({'role':'assistant','text':'Maestro Radarr Sonarr'})
        (self.chats / 'assistant.json').write_text(json.dumps(data))
        self.assertEqual(web.organize_idle_chats(10000), [])
        self.assertEqual(self.read('manual')['folder_id'], 'web')

    def test_maestro_remains_classifiable_after_folder_persisted(self):
        self.chat('first', 'Orquestrador Maestro')
        web.organize_idle_chats(10000)
        self.chat('second', 'Orquestrador')
        web.organize_idle_chats(10000)
        self.assertEqual(self.read('first')['folder_id'], self.read('second')['folder_id'])


if __name__ == '__main__':
    unittest.main()
