import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import maestro_queue as Q
import maestro_web as web


class StopKillsOrphansTest(unittest.TestCase):
    def test_live_agent_of_finished_turn_is_killed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proc = subprocess.Popen(['sleep', '60'], start_new_session=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        child = {'chat_id': 'chat-orfao', 'status': 'done', 'proc': proc}
        jobs = {'old': {'chat_id': 'chat-orfao', 'status': 'done', 'proc': None, 'children': [child]}}
        with patch.object(web, 'TURN_QUEUE', Q.TurnQueue(pathlib.Path(tmp.name) / 'q.json')), patch.object(web, 'ACTIVE_JOBS', jobs):
            web.stop_chat_job('chat-orfao')
        self.assertIsNotNone(proc.wait(timeout=5))
        self.assertEqual(child['status'], 'stopped')


if __name__ == '__main__':
    unittest.main()
