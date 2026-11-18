"""Cloud-compatible desktop handoff. No Bambu cloud or printer I/O occurs."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app
from app.mesh import ModelError, inspect_3mf
from test_api import binary_stl, model_3mf, sliced_job


class StudioApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / 'data'
        self.app = create_app(data_dir=self.data, seed_dir='')
        self.client = TestClient(self.app)
        self.client.__enter__()
        csrf = self.client.get('/api/session').json()['csrf_token']
        self.headers = {'X-CSRF-Token': csrf, 'Origin': 'http://testserver'}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def model(self, content=None, filename='pot.stl'):
        response = self.client.post('/api/models/upload', headers=self.headers,
            files={'files': (filename, content or binary_stl(), 'application/octet-stream')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['errors'], [])
        return response.json()['items'][0]

    def prepare(self, item):
        response = self.client.post(f"/api/models/{item['id']}/studio", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def job(self, item, printer='Bambu Lab P2S'):
        content = sliced_job(printer)
        response = self.client.post(f"/api/models/{item['id']}/jobs", headers=self.headers,
            files={'file': ('P2S & plate.gcode.3mf', content, 'application/octet-stream')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), content

    def test_default_and_saved_cloud_mode_need_no_ip_code_or_login(self):
        public = self.client.get('/api/printer').json()
        self.assertEqual(public['mode'], 'cloud')
        self.assertFalse(public['configured'])
        self.assertFalse(public['verified'])
        with patch('app.printer.test_connection') as connect:
            response = self.client.put('/api/printer', headers=self.headers,
                json={'name': 'My P2S', 'mode': 'cloud'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['access_code_saved'])
            self.assertEqual(self.client.post('/api/printer/test', headers=self.headers).status_code, 409)
            connect.assert_not_called()
        private = json.loads((self.data/'printer.json').read_text())
        self.assertEqual(private, {'name': 'My P2S', 'mode': 'cloud'})
        self.assertEqual((self.data/'printer.json').stat().st_mode & 0o777, 0o600)
        status = self.client.get('/api/printer/status').json()
        self.assertEqual(status['state'], 'managed_in_studio')
        self.assertFalse(status['available'])

    def test_upgrade_defaults_to_cloud_without_destroying_old_lan_config(self):
        old = {'name': 'P2S', 'host': '10.20.30.90', 'serial': 'TEST1234',
               'access_code': 'PRIVATE_CODE', 'fingerprint': 'ab'*32}
        self.app.state.store.save_printer(old)
        before = (self.data/'printer.json').read_bytes()
        response = self.client.get('/api/printer')
        self.assertEqual(response.json()['mode'], 'cloud')
        self.assertTrue(response.json()['lan_verified'])
        self.assertFalse(response.json()['verified'])
        self.assertNotIn('PRIVATE_CODE', response.text)
        self.assertEqual((self.data/'printer.json').read_bytes(), before)
        response = self.client.put('/api/printer', headers=self.headers,
            json={'mode':'cloud', 'name':'Renamed P2S'})
        self.assertEqual(response.status_code, 200)
        after = json.loads((self.data/'printer.json').read_text())
        for key in ('host','serial','access_code','fingerprint'):
            self.assertEqual(after[key], old[key])

    def test_lan_is_explicit_and_its_existing_validation_remains(self):
        response = self.client.put('/api/printer', headers=self.headers,
            json={'mode':'cloud', 'host':'invalid unused value'})
        self.assertEqual(response.status_code, 200)
        for body in ({'mode':'direct_cloud'}, {'mode':'lan', 'host':'8.8.8.8', 'access_code':'test'},
                     {'mode':'lan', 'host':'10.20.30.90'}):
            self.assertIn(self.client.put('/api/printer', headers=self.headers, json=body).status_code, (400,422))
        response = self.client.put('/api/printer', headers=self.headers,
            json={'mode':'lan', 'host':'10.20.30.90', 'access_code':'PRIVATE_CODE'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['mode'], 'lan')
        self.assertTrue(response.json()['configured'])
        self.assertFalse(response.json()['verified'])

    def test_cloud_mode_does_not_attempt_a_direct_printer_transfer(self):
        item = self.model()
        job, _ = self.job(item)
        self.app.state.store.save_printer({'host':'10.20.30.90','access_code':'PRIVATE_CODE',
                                          'fingerprint':'ab'*32})
        with patch('app.printer.upload_file') as transfer:
            response = self.client.post(f"/api/jobs/{job['id']}/send", headers=self.headers, json={})
            self.assertEqual(response.status_code, 409)
            self.assertIn('Studio', response.json()['detail'])
            transfer.assert_not_called()
        with self.app.state.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM transfers').fetchone()[0], 0)

    def test_stl_conversion_is_cookie_free_download_and_preserves_original(self):
        source = binary_stl()
        item = self.model(source, filename='Pöt & file=evil?#%.stl')
        ready = self.prepare(item)
        self.assertTrue(ready['converted'])
        self.assertTrue(ready['download_url'].endswith('.3mf'))
        self.assertTrue(ready['download_url'].isascii())
        for char in ('?', '#', '%', '&', '='):
            self.assertNotIn(char, ready['download_url'])
        peer = TestClient(self.app)
        try:
            response = peer.get(ready['download_url'])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['content-type'], 'model/3mf')
            self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
            self.assertFalse(peer.cookies)
        finally:
            peer.close()
        path = Path(self.temp.name)/'roundtrip.3mf'
        path.write_bytes(response.content)
        geometry = inspect_3mf(path)
        self.assertEqual(geometry['triangle_count'], item['triangle_count'])
        self.assertEqual(geometry['dimensions'], item['dimensions'])
        self.assertEqual(self.client.get(item['download_url']).content, source)

    def test_existing_3mf_is_returned_byte_for_byte_with_project_metadata(self):
        source = model_3mf(extra_entries=[('Metadata/project_settings.config', '{"printer_model":"P2S"}')])
        item = self.model(source, 'colour project.3mf')
        ready = self.prepare(item)
        self.assertFalse(ready['converted'])
        self.assertEqual(self.client.get(ready['download_url']).content, source)
        self.assertEqual(list(self.app.state.store.studio.iterdir()), [])

    def test_obj_uses_same_geometry_and_original_remains_available(self):
        source = b'v 0 0 0\nv 10 0 0\nv 10 10 0\nv 4 4 0\nv 0 10 0\nf -5 -4 -3 -2 -1\n'
        item = self.model(source, 'concave.obj')
        ready = self.prepare(item)
        path = Path(self.temp.name)/'obj.3mf'
        path.write_bytes(self.client.get(ready['download_url']).content)
        mesh = inspect_3mf(path)
        self.assertEqual(mesh['triangle_count'], 3)
        self.assertEqual(mesh['dimensions'], item['dimensions'])
        self.assertEqual(self.client.get(item['download_url']).content, source)

    def test_preparation_requires_csrf_but_original_download_does_not(self):
        item = self.model()
        route = f"/api/models/{item['id']}/studio"
        self.assertEqual(self.client.post(route).status_code, 403)
        self.assertEqual(self.client.post(route, headers=self.headers|{'Origin':'http://elsewhere'}).status_code, 403)
        self.assertEqual(self.client.get(item['download_url']).status_code, 200)
        row = self.app.state.store.row(item['id'])
        url = route+'/'+self.app.state.store.studio_filename(row)
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_prepared_copy_is_reused_and_wrong_filename_is_rejected(self):
        item = self.model()
        from app.studio_export import export_model_3mf
        with patch('app.studio_export.export_model_3mf', wraps=export_model_3mf) as export:
            first = self.prepare(item)
            second = self.prepare(item)
            self.assertEqual(first, second)
            export.assert_called_once()
        wrong = first['download_url'].rsplit('/',1)[0]+'/another.3mf'
        self.assertEqual(self.client.get(wrong).status_code, 404)

    def test_failed_conversion_keeps_original_and_returns_helpful_error(self):
        item = self.model()
        with patch('app.studio_export.export_model_3mf', side_effect=ModelError('Conversion budget exceeded. Download the original.')):
            response = self.client.post(f"/api/models/{item['id']}/studio", headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Download the original', response.json()['detail'])
        self.assertEqual(self.client.get(item['download_url']).content, binary_stl())
        self.assertEqual(list(self.app.state.store.studio.iterdir()), [])

    def test_sliced_job_handoff_keeps_original_and_does_not_start_printing(self):
        item = self.model()
        job, source = self.job(item)
        with patch('app.printer.upload_file') as send:
            response = self.client.post(f"/api/jobs/{job['id']}/studio", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            ready = response.json()
            self.assertTrue(ready['sliced'])
            self.assertFalse(ready['converted'])
            self.assertTrue(ready['download_url'].endswith('.gcode.3mf'))
            self.assertEqual(self.client.get(ready['download_url']).content, source)
            send.assert_not_called()
        wrong, _ = self.job(item, printer='Bambu Lab A1')
        self.assertEqual(self.client.post(f"/api/jobs/{wrong['id']}/studio", headers=self.headers).status_code, 400)

    def test_long_sliced_names_keep_the_complete_gcode_suffix(self):
        item = self.model()
        source = sliced_job()
        response = self.client.post(f"/api/models/{item['id']}/jobs", headers=self.headers,
            files={'file': ('P2S '+('x'*160)+'.gcode.3mf', source, 'application/octet-stream')})
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()
        ready = self.client.post(f"/api/jobs/{job['id']}/studio", headers=self.headers).json()
        self.assertTrue(ready['filename'].endswith('.gcode.3mf'))
        self.assertEqual(self.client.get(ready['download_url']).content, source)

    def test_trash_hides_prepared_model_and_job_urls(self):
        item = self.model()
        ready = self.prepare(item)
        job, _ = self.job(item)
        job_ready = self.client.post(f"/api/jobs/{job['id']}/studio", headers=self.headers).json()
        self.assertEqual(self.client.delete(f"/api/models/{item['id']}", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(ready['download_url']).status_code, 404)
        self.assertEqual(self.client.get(job_ready['download_url']).status_code, 404)
        self.assertEqual(self.client.post(f"/api/models/{item['id']}/studio", headers=self.headers).status_code, 404)


if __name__ == '__main__':
    unittest.main()
