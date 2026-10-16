import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from mock_player.server import create_app
from mock_player.engine import second_data
from mock_player.aggregation import aggregate
from mock_player.storage import parse_time


class ClearHistoryTests(unittest.TestCase):
    def test_confirmation_and_playback_guard(self):
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(root)) as client:
            self.assertEqual(client.post('/v2/player/clear-history', json={}).status_code, 400)
            client.app.state.store.update(state='running')
            self.assertEqual(client.post('/v2/player/clear-history', json={'confirm': 'CLEAR_PLAYER_HISTORY'}).status_code, 409)

    def test_backup_reset_replay_and_active_task_guard(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app(root)
            with TestClient(app) as client:
                store = app.state.store
                stamp = datetime(2026, 10, 14, tzinfo=timezone.utc)
                batches = second_data('B', 0, stamp)
                store.commit('B', 0, batches, aggregate(0, batches['cv'], batches['sensor'], batches['position']), parse_time(stamp.isoformat()), False)
                task = client.post('/v2/tasks/claim', json={'window_seconds':1}).json()['task']
                self.assertEqual(client.post('/v2/player/clear-history', json={'confirm': 'CLEAR_PLAYER_HISTORY'}).status_code, 409)
                client.post('/v2/tasks/' + str(task['task_id']) + '/fail', json={'lease_token': task['lease_token'], 'error': {'message': 'test finished'}, 'terminal': True})
                before = client.get('/v2/data/status').json()['counts']
                with store.db() as db:
                    last_id = db.execute('SELECT MAX(id) FROM cv_observations').fetchone()[0]
                result = client.post('/v2/player/clear-history', json={'confirm': 'CLEAR_PLAYER_HISTORY'})
                self.assertEqual(result.status_code, 200, result.text)
                backup = Path(root) / result.json()['backup']
                with closing(sqlite3.connect(backup)) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM cv_observations').fetchone()[0], before['cv'])
                self.assertTrue(all(v == 0 for v in client.get('/v2/data/status').json()['counts'].values()))
                self.assertEqual(client.get('/v2/player/status').json()['state'], 'idle')
                self.assertEqual(client.get('/v2/consumers').status_code,404)
                self.assertEqual(client.get('/v2/tasks').json()['items'], [])
                self.assertEqual(client.get('/v2/results').json()['items'], [])
                store.commit('B', 0, batches, aggregate(0, batches['cv'], batches['sensor'], batches['position']), parse_time(stamp.isoformat()), False)
                with store.db() as db:
                    self.assertGreater(db.execute('SELECT MIN(id) FROM cv_observations').fetchone()[0], last_id)
                self.assertTrue(client.post('/v2/tasks/claim', json={'window_seconds':1}).json()['task'])
            with TestClient(create_app(root)) as client:
                self.assertGreater(client.get('/v2/data/status').json()['counts']['cv'], 0)
