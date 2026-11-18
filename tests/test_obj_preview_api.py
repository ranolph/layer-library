"""OBJ upload and missing/blank thumbnail regressions against isolated stores."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.mesh import ModelError
from app.thumbnails import render_thumbnail
from test_api import binary_stl, model_3mf


OBJ = b'''# Printable tetrahedron; materials are optional
mtllib absent.mtl
o Tetrahedron
v 0 0 0
v 10 0 0
v 0 20 0
v 0 0 30
usemtl beige
f 1 3 2
f 1 2 4
f 1 4 3
f -3 -2 -1
'''


class ObjPreviewApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.app = create_app(data_dir=self.data, seed_dir='')
        self.client = TestClient(self.app)
        self.client.__enter__()
        session = self.client.get('/api/session').json()
        self.headers = {'X-CSRF-Token': session['csrf_token'], 'Origin': 'http://testserver'}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def upload(self, content=OBJ, filename='Tetrahedron.OBJ'):
        response = self.client.post('/api/models/upload', headers=self.headers,
            files={'files': (filename, content, 'application/octet-stream')})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['errors'], [], body)
        return body['items'][0]

    def assert_mesh_png(self, response):
        self.assertEqual(response.status_code, 200, response.text[:200] if response.status_code != 200 else '')
        self.assertEqual(response.headers['content-type'], 'image/png')
        with Image.open(io.BytesIO(response.content)) as image:
            self.assertEqual(image.size, (640, 480))
            self.assertTrue(any(high-low > 30 for low,high in image.convert('RGB').getextrema()))

    def test_obj_upload_original_download_dimensions_and_statistics(self):
        item = self.upload()
        self.assertEqual(item['kind'], 'obj')
        self.assertEqual(item['dimensions'], {'x': 10.0, 'y': 20.0, 'z': 30.0})
        self.assertEqual(item['triangle_count'], 4)
        self.assertEqual(self.client.get(item['download_url']).content, OBJ)
        self.assertEqual(self.client.get('/api/library').json()['stats']['obj_count'], 1)
        self.assert_mesh_png(self.client.get(item['thumbnail_url']))

    def test_obj_invalid_geometry_is_an_upload_error_without_storing_it(self):
        response = self.client.post('/api/models/upload', headers=self.headers,
            files={'files': ('broken.obj', b'v 0 0 0\nf 0 1 2\n', 'text/plain')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['items'], [])
        self.assertTrue(response.json()['errors'])
        self.assertEqual(list((self.data/'files').iterdir()), [])
        self.assertEqual(list((self.data/'incoming').iterdir()), [])

    def test_missing_stl_thumbnail_is_created_without_opening_viewer_or_reuploading(self):
        original = binary_stl()
        item = self.upload(original, 'existing.stl')
        self.assertFalse(item['thumbnail_ready'])
        listing = self.client.get('/api/library').json()['items'][0]
        self.assertTrue(listing['thumbnail_url'])
        self.assert_mesh_png(self.client.get(listing['thumbnail_url']))
        self.assertTrue(self.client.get('/api/library').json()['items'][0]['thumbnail_ready'])
        self.assertEqual(self.client.get(item['download_url']).content, original)

    def test_old_blank_or_corrupt_thumbnail_is_replaced_automatically(self):
        item = self.upload(binary_stl(), 'existing.stl')
        path = self.data/'thumbnails'/f"{item['id']}.png"
        for content in (b'incomplete image', None):
            if content is None:
                Image.new('RGB', (640, 480), '#eceae3').save(path)
            else:
                path.write_bytes(content)
            with self.subTest(content=content):
                detail = self.client.get(f"/api/models/{item['id']}").json()
                self.assertFalse(detail['thumbnail_ready'])
                self.assert_mesh_png(self.client.get(item['thumbnail_url']))

    def test_concurrent_thumbnail_requests_render_the_same_model_once(self):
        item = self.upload()
        with patch('app.main.render_thumbnail', wraps=render_thumbnail) as render:
            with ThreadPoolExecutor(max_workers=3) as workers:
                results = list(workers.map(lambda _: self.client.get(item['thumbnail_url']), range(3)))
            self.assertEqual(render.call_count, 1)
        for response in results:
            self.assert_mesh_png(response)

    def test_rebuild_requires_csrf_and_success_returns_new_image_url(self):
        item = self.upload()
        path = f"/api/models/{item['id']}/thumbnail/rebuild"
        self.assertEqual(self.client.post(path).status_code, 403)
        response = self.client.post(path, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['thumbnail_ready'])
        self.assertIn('?v=', response.json()['thumbnail_url'])
        self.assert_mesh_png(self.client.get(response.json()['thumbnail_url']))

    def test_failed_rebuild_preserves_previous_thumbnail_and_original_model(self):
        item = self.upload()
        image = self.client.get(item['thumbnail_url']).content
        path = f"/api/models/{item['id']}/thumbnail/rebuild"
        with patch('app.main.render_thumbnail', side_effect=ModelError('Render budget exceeded; try a simpler preview.')):
            failed = self.client.post(path, headers=self.headers)
        self.assertEqual(failed.status_code, 422)
        self.assertIn('budget', failed.json()['detail'])
        self.assertEqual(self.client.get(item['thumbnail_url']).content, image)
        self.assertEqual(self.client.get(item['download_url']).content, OBJ)

    def test_missing_thumbnail_errors_are_cooled_down_but_rebuild_can_retry(self):
        item = self.upload()
        with patch('app.main.render_thumbnail', side_effect=ModelError('Preview temporarily unavailable.')) as render:
            self.assertEqual(self.client.get(item['thumbnail_url']).status_code, 422)
            self.assertEqual(self.client.get(item['thumbnail_url']).status_code, 422)
            self.assertEqual(render.call_count, 1)
        rebuilt = self.client.post(f"/api/models/{item['id']}/thumbnail/rebuild", headers=self.headers)
        self.assertEqual(rebuilt.status_code, 200, rebuilt.text)
        self.assert_mesh_png(self.client.get(item['thumbnail_url']))

    def test_browser_fallback_does_not_overwrite_server_thumbnail(self):
        item = self.upload()
        generated = self.client.get(item['thumbnail_url']).content
        image = Image.new('RGB', (100, 100), '#102040')
        image.paste('#f08020', (20, 20, 80, 80))
        stream = io.BytesIO(); image.save(stream, 'PNG')
        response = self.client.post(item['thumbnail_url'], headers=self.headers, content=stream.getvalue())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(item['thumbnail_url']).content, generated)

    def test_blank_browser_capture_is_rejected(self):
        item = self.upload()
        stream = io.BytesIO()
        Image.new('RGB', (640, 480), '#eceae3').save(stream, 'PNG')
        response = self.client.post(item['thumbnail_url'], headers=self.headers, content=stream.getvalue())
        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.data/'thumbnails'/f"{item['id']}.png").exists())

    def test_large_preview_opt_in_also_applies_to_obj_and_stl(self):
        for content, name in ((OBJ, 'large.obj'), (binary_stl(), 'large.stl')):
            item = self.upload(content, name)
            with self.app.state.store.db() as db:
                db.execute('UPDATE models SET triangle_count=1000001 WHERE id=?', (item['id'],))
            self.assertTrue(self.client.get(f"/api/models/{item['id']}").json()['large_preview'])

    def test_3mf_without_embedded_image_can_still_download_and_accept_browser_capture(self):
        content = model_3mf()
        item = self.upload(content, 'assembly.3mf')
        self.assertEqual(self.client.get(item['thumbnail_url']).status_code, 422)
        self.assertEqual(self.client.get(item['download_url']).content, content)
        image = Image.new('RGB', (640, 480), '#eceae3')
        image.paste('#a87030', (100, 80, 540, 400))
        stream = io.BytesIO(); image.save(stream, 'PNG')
        captured = self.client.post(item['thumbnail_url'], headers=self.headers, content=stream.getvalue())
        self.assertEqual(captured.status_code, 200, captured.text)
        self.assert_mesh_png(self.client.get(item['thumbnail_url']))


if __name__ == '__main__':
    unittest.main()
