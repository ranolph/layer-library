"""Fresh public installations and Featured metadata on persistent stores."""
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app
from test_api import binary_stl


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        environment = patch.dict(os.environ, {
            'DATA_DIR': str(self.data),
            'BACKUP_SERVICE_ORIGIN': '',
            'SEED_DIR': '',
        })
        environment.start()
        self.addCleanup(environment.stop)

    def session(self, client):
        session = client.get('/api/session')
        self.assertEqual(session.status_code, 200)
        return {'X-CSRF-Token': session.json()['csrf_token']}

    def upload(self, client, headers):
        response = client.post('/api/models/upload', headers=headers,
                               files=[('files', ('test.stl', binary_stl(), 'application/octet-stream'))])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['errors'], [])
        return response.json()['items'][0]

    def test_fresh_install_uses_configured_storage_and_starts_empty(self):
        app = create_app()
        with TestClient(app) as client:
            self.assertIsNone(app.state.store.seed)
            self.assertEqual(app.state.store.data, self.data)
            self.assertTrue((self.data / 'library.sqlite3').is_file())
            self.assertEqual(client.get('/api/library').json()['items'], [])
            session = client.get('/api/session').json()
            self.assertEqual(session['backup_service_origin'], '')
            self.assertFalse(session['login_required'])

    def test_standalone_default_data_path_does_not_require_root(self):
        with patch.dict(os.environ, {}, clear=True):
            app = create_app()
        try:
            self.assertEqual(app.state.store.data, Path('data'))
            self.assertIsNone(app.state.store.seed)
        finally:
            app.state.store.pool.shutdown()

    def test_featured_survives_metadata_edits_trash_restore_and_restart(self):
        with TestClient(create_app()) as client:
            headers = self.session(client)
            item = self.upload(client, headers)
            mid = item['id']
            self.assertFalse(item['featured'])
            response = client.put(f'/api/models/{mid}/featured', headers=headers,
                                  json={'featured': True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['featured'])
            edited = client.put(f'/api/models/{mid}', headers=headers,
                                json={'title': 'Tetrahedron', 'collection': 'Samples',
                                      'tags': ['test', 'PLA'], 'notes': 'An independent test model.'})
            self.assertTrue(edited.json()['featured'])
            self.assertEqual(client.get('/api/library').json()['stats']['featured_count'], 1)
            self.assertEqual(client.delete(f'/api/models/{mid}', headers=headers).status_code, 200)
            self.assertEqual(client.get('/api/library').json()['stats']['featured_count'], 0)
            restored = client.post(f'/api/models/{mid}/restore', headers=headers)
            self.assertTrue(restored.json()['featured'])
        with TestClient(create_app()) as client:
            library = client.get('/api/library').json()
            self.assertEqual(library['stats']['models'], 1)
            self.assertEqual(library['stats']['featured_count'], 1)
            self.assertEqual(library['items'][0]['tags'], ['test', 'PLA'])
            self.assertEqual(client.get(f'/api/models/{mid}/file').content, binary_stl())

    def test_featured_requires_real_boolean_and_browser_session(self):
        with TestClient(create_app()) as client:
            headers = self.session(client)
            mid = self.upload(client, headers)['id']
            endpoint = f'/api/models/{mid}/featured'
            self.assertEqual(client.put(endpoint, json={'featured': True}).status_code, 403)
            for invalid in ('true', 1, 0, None):
                with self.subTest(value=invalid):
                    self.assertEqual(client.put(endpoint, headers=headers,
                                                json={'featured': invalid}).status_code, 422)
            self.assertEqual(client.put(endpoint, headers=headers,
                                        json={'featured': False}).status_code, 200)

    def test_existing_database_migrates_without_losing_models(self):
        with TestClient(create_app()) as client:
            item = self.upload(client, self.session(client))
        with sqlite3.connect(self.data / 'library.sqlite3') as database:
            database.execute('ALTER TABLE models DROP COLUMN featured')
        with TestClient(create_app()) as client:
            stored = client.get(f'/api/models/{item["id"]}').json()
            self.assertEqual(stored['filename'], 'test.stl')
            self.assertFalse(stored['featured'])
            self.assertEqual(client.get(stored['download_url']).content, binary_stl())


if __name__ == '__main__':
    unittest.main()
