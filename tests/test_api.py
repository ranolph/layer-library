"""API and mesh-inspection integration tests using isolated temporary stores.

No real printer or NAS is contacted. Printer I/O is mocked at its module API.
Run from the package directory: .venv/bin/python -m unittest discover -s tests
"""
from __future__ import annotations

import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import warnings
import zipfile

from fastapi.testclient import TestClient
from PIL import Image

from app.main import COOKIE, VERSION, create_app, validate_backup_origin
from app.mesh import inspect_stl as original_inspect_stl


PIN = 'ab' * 32


def binary_stl(dx=10, dy=20, dz=30):
    vertices = [(0, 0, 0), (dx, 0, 0), (0, dy, 0), (0, 0, dz)]
    faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
    data = bytearray(b'Independent test tetrahedron'.ljust(80, b' '))
    data.extend(struct.pack('<I', len(faces)))
    for face in faces:
        data.extend(struct.pack('<12fH', 0, 0, 0, *(n for index in face for n in vertices[index]), 0))
    return bytes(data)


def ascii_stl():
    return b'''solid test
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 12 0 0
vertex 0 8 0
endloop
endfacet
endsolid test
'''


def archive_bytes(entries, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=compression) as archive:
        for name, value in entries:
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
            info.compress_type=compression
            archive.writestr(info, value)
    return buffer.getvalue()


def model_3mf(unit='millimeter', extra_entries=None):
    # Parent/child composition: mesh X=[0,2], component translates by5,
    # its parent scales X by2, build translates by10 => X=[20,24].
    # Another mesh is built at the origin, so combined dimensions are24×3×4.
    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="{unit}">
 <resources>
  <object id="1" type="model"><mesh>
   <vertices><vertex x="0" y="0" z="0"/><vertex x="2" y="0" z="0"/>
    <vertex x="0" y="3" z="0"/><vertex x="0" y="0" z="4"/></vertices>
   <triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/>
    <triangle v1="0" v2="3" v3="2"/><triangle v1="1" v2="2" v3="3"/></triangles>
  </mesh></object>
  <object id="2" type="model"><components>
   <component objectid="1" transform="1 0 0 0 1 0 0 0 1 5 0 0"/>
  </components></object>
  <object id="3" type="model"><components>
   <component objectid="2" transform="2 0 0 0 1 0 0 0 1 0 0 0"/>
  </components></object>
 </resources>
 <build><item objectid="1"/><item objectid="3" transform="1 0 0 0 1 0 0 0 1 10 0 0"/></build>
</model>'''
    entries = [('3D/3dmodel.model', xml)]
    entries.extend(extra_entries or [])
    return archive_bytes(entries)


def sliced_job(printer='Bambu Lab P2S', *, settings=None, header_printer=True):
    if settings is None:
        settings = {'printer_model': printer, 'nozzle_diameter': ['0.4']}
    header = f'; printer_model = {printer}\n' if header_printer else '; fixture sliced by Bambu Studio\n'
    return archive_bytes([
        ('Metadata/project_settings.config', json.dumps(settings)),
        ('Metadata/plate_1.gcode', header + '; nozzle_diameter = 0.4\nG90\nG1 X10 Y20 Z0.2\n'),
    ])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name) / 'data'
        self.app = create_app(data_dir=self.data, seed_dir='')
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.csrf = None

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def session(self):
        response = self.client.get('/api/session')
        self.assertEqual(response.status_code, 200, response.text)
        self.csrf = response.json()['csrf_token']
        return response

    @property
    def headers(self):
        return {'X-CSRF-Token': self.csrf or '', 'Origin': 'http://testserver'}

    def upload(self, content=None, filename='pot.stl'):
        return self.client.post('/api/models/upload', headers=self.headers,
                                files={'files': (filename, content or binary_stl(), 'application/octet-stream')})

    def model(self, content=None, filename='pot.stl'):
        response = self.upload(content, filename)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['errors'], [], body)
        self.assertEqual(len(body['items']), 1)
        return body['items'][0]

    def job(self, model_id, content=None):
        response = self.client.post(f'/api/models/{model_id}/jobs', headers=self.headers,
                                    files={'file': ('pot.gcode.3mf', content or sliced_job(), 'application/octet-stream')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def configure_printer(self):
        response = self.client.put('/api/printer', headers=self.headers,
                                   json={'mode': 'lan', 'host': '192.168.10.42', 'name': 'Test P2S',
                                         'serial': 'TEST12345', 'access_code': 'testLANaccess'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('testLANaccess', response.text)
        return response.json()

    def verify_printer(self):
        with patch('app.printer.test_connection', return_value={'ok': True, 'certificate_fingerprint': PIN}) as test:
            response = self.client.post('/api/printer/test', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        test.assert_called_once_with('192.168.10.42', 'testLANaccess', None)

    def wait_transfer(self, transfer_id):
        for _ in range(100):
            response = self.client.get(f'/api/transfers/{transfer_id}')
            self.assertEqual(response.status_code, 200)
            result = response.json()
            if result['status'] in {'complete', 'failed'}:
                return result
            time.sleep(.01)
        self.fail('Mocked transfer did not finish')

    def test_fresh_startup_and_reading_need_no_password_or_browser_session(self):
        self.assertIsNone(self.app.state.store.setting('password_hash'))
        self.assertEqual(self.client.get('/api/health').json()['version'], VERSION)
        for path in ['/api/library', '/api/trash', '/api/printer']:
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertNotIn(COOKIE, self.client.cookies)
        for path in ['/api/models/absent/file', '/api/transfers/absent']:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.upload().status_code, 403)

    def test_anonymous_csrf_session_cookie_origin_and_legacy_status(self):
        response = self.session()
        self.assertFalse(response.json()['login_required'])
        self.assertEqual(response.json()['access_mode'], 'trusted_lan')
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=strict', response.headers['set-cookie'])
        first_cookie = self.client.cookies.get(COOKIE)
        legacy = self.client.get('/api/auth/status')
        self.assertTrue(legacy.json()['authenticated'])
        self.assertFalse(legacy.json()['setup_required'])
        self.assertEqual(legacy.json()['csrf_token'], self.csrf)
        self.assertEqual(self.client.cookies.get(COOKIE), first_cookie)
        for headers in (
            {}, {'X-CSRF-Token': 'wrong'},
            self.headers | {'Origin': 'http://foreign.example'},
            self.headers | {'Origin': 'https://testserver'},
            self.headers | {'Origin': 'http://testserver/path'},
            self.headers | {'Origin': 'null'},
            self.headers | {'Sec-Fetch-Site': 'cross-site'},
            self.headers | {'Sec-Fetch-Site': 'same-site'},
        ):
            with self.subTest(headers=headers):
                rejected = self.client.post('/api/models/upload', headers=headers,
                    files={'files': ('pot.stl', binary_stl(), 'application/octet-stream')})
                self.assertEqual(rejected.status_code, 403)
        self.assertEqual(self.client.get('/api/library').json()['stats']['models'], 0)
        self.assertEqual(self.upload().json()['errors'], [])

    def test_sessions_are_per_browser_but_model_downloads_are_public_on_lan(self):
        self.session()
        item = self.model()
        peer = TestClient(self.app)
        try:
            self.assertEqual(peer.get('/api/library').status_code, 200)
            self.assertEqual(peer.get(item['download_url']).content, binary_stl())
            self.assertNotIn(COOKIE, peer.cookies)
            peer_session = peer.get('/api/session').json()
            self.assertNotEqual(peer_session['csrf_token'], self.csrf)
            self.assertNotEqual(peer.cookies.get(COOKIE), self.client.cookies.get(COOKIE))
            self.assertEqual(peer.delete(f"/api/models/{item['id']}", headers=self.headers).status_code, 403)
            headers = {'X-CSRF-Token': peer_session['csrf_token'], 'Origin': 'http://testserver'}
            self.assertEqual(peer.delete(f"/api/models/{item['id']}", headers=headers).status_code, 200)
        finally:
            peer.close()

    def test_expired_session_is_renewed_without_login_and_cross_origin_bootstrap_is_blocked(self):
        self.session()
        expired = self.csrf
        with self.app.state.store.db() as db:
            db.execute('UPDATE sessions SET expires=0')
        self.assertEqual(self.client.get('/api/library').status_code, 200)
        self.assertEqual(self.upload().status_code, 403)
        self.session()
        self.assertNotEqual(self.csrf, expired)
        self.assertEqual(self.upload().json()['errors'], [])
        self.client.cookies.clear()
        for headers in ({'Origin': 'http://foreign.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            rejected = self.client.get('/api/session', headers=headers)
            self.assertEqual(rejected.status_code, 403)
            self.assertNotIn('set-cookie', rejected.headers)
        self.assertNotIn(COOKIE, self.client.cookies)

    def test_login_controls_are_removed_and_backup_button_is_present(self):
        for path in ('/api/auth/login', '/api/auth/logout', '/api/auth/password'):
            self.assertEqual(self.client.post(path, json={}).status_code, 404)
        page = self.client.get('/').text
        for former_id in ('loginScreen', 'loginForm', 'logoutButton', 'passwordForm'):
            self.assertNotIn('id="' + former_id + '"', page)
        self.assertIn('data-site-backup="layer-library"', page)
        self.assertIn('Back up to Google Drive', page)
        self.assertIn('<script src="/static/site-backup.js" defer></script>', page)
        self.assertIn('id="printerCode"', page)

    def test_backup_service_origin_is_exposed_and_scoped_to_connect_src(self):
        response = self.session()
        origin = response.json()['backup_service_origin']
        self.assertEqual(origin, '')
        self.assertEqual(response.json()['version'], VERSION)
        directives = {part.strip().split(' ', 1)[0]: part.strip().split(' ', 1)[1]
                      for part in response.headers['content-security-policy'].split(';') if part.strip()}
        self.assertEqual(directives['connect-src'], "'self'")
        self.assertEqual(directives['script-src'], "'self'")
        self.assertEqual(directives['object-src'], "'none'")
        with patch.dict(os.environ, {'BACKUP_SERVICE_ORIGIN': 'https://backup.example:9443'}):
            replacement = create_app(data_dir=self.data / 'custom-origin', seed_dir='')
        with TestClient(replacement) as client:
            custom = client.get('/api/session')
            self.assertEqual(custom.json()['backup_service_origin'], 'https://backup.example:9443')
            self.assertIn("connect-src 'self' https://backup.example:9443;",
                          custom.headers['content-security-policy'])

    def test_backup_origin_configuration_rejects_csp_injection_and_non_origins(self):
        self.assertEqual(validate_backup_origin('http://10.20.30.200:8782'), 'http://10.20.30.200:8782')
        self.assertEqual(validate_backup_origin('https://[::1]:8782'), 'https://[::1]:8782')
        for value in ('*', 'https://*.example', 'file:///tmp', 'javascript:alert(1)',
                      'https://user:secret@example.com', 'https://example.com/',
                      'https://example.com/path', 'https://example.com?query=1',
                      'https://example.com#fragment', 'http://example.com:0',
                      'http://example.com:65536', "https://example.com;script-src *",
                      'https://example.com\\path', 'https://example.com\n', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_backup_origin(value)

    def test_valid_binary_ascii_dimensions_and_p2s_fit(self):
        self.session()
        item = self.model()
        self.assertEqual(item['dimensions'], {'x':10.0, 'y':20.0, 'z':30.0})
        self.assertEqual(item['triangle_count'], 4)
        self.assertTrue(item['fit_p2s'])
        flat = self.model(ascii_stl(), 'flat.stl')
        self.assertEqual(flat['dimensions'], {'x':12.0, 'y':8.0, 'z':0.0})
        large = self.model(binary_stl(257,20,30), 'large.stl')
        self.assertFalse(large['fit_p2s'])

    def test_invalid_stl_errors_leave_no_files_or_incoming_parts(self):
        self.session()
        for content in [b'not an STL', binary_stl()[:-1], binary_stl(float('nan'),20,30),
                        b'solid broken\nvertex 0 0 0\nendsolid broken\n']:
            response = self.upload(content, 'broken.stl')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['items'], [])
            self.assertEqual(len(response.json()['errors']), 1)
        self.assertEqual(self.client.get('/api/library').json()['stats']['models'], 0)
        self.assertEqual(list((self.data/'incoming').iterdir()), [])
        self.assertEqual(list((self.data/'files').iterdir()), [])

    def test_actual_starlette_multipart_upload_and_safe_filename(self):
        self.session()
        response = self.client.post('/api/models/upload', headers=self.headers,
                                    files=[('files', ('../../outside.stl', binary_stl(), 'application/octet-stream')),
                                           ('files', ('C:\\fakepath\\another.stl', binary_stl(11,20,30), 'application/octet-stream'))],
                                    data={'collection':'Plant pots'})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['errors'], [])
        self.assertEqual([x['filename'] for x in body['items']], ['outside.stl','another.stl'])
        self.assertTrue(all(x['collection']=='Plant pots' for x in body['items']))
        self.assertFalse((self.data.parent/'outside.stl').exists())
        self.assertTrue(all(p.name.endswith('.stl') and len(p.stem)==32 for p in (self.data/'files').iterdir()))
        item = body['items'][0]
        self.assertEqual(self.client.get(item['download_url']).content, binary_stl())

    def test_duplicate_trash_restore_and_original_bytes_are_preserved(self):
        self.session()
        item = self.model()
        duplicate = self.model(binary_stl(), 'different-name.stl')
        self.assertEqual(item['id'], duplicate['id'])
        self.assertEqual(self.client.get('/api/library').json()['stats']['models'], 1)
        mid = item['id']
        self.assertEqual(self.client.delete(f'/api/models/{mid}',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/library').json()['stats']['models'],0)
        self.assertEqual(self.client.get(item['download_url']).status_code,404)
        self.assertEqual(len(self.client.get('/api/trash').json()['items']),1)
        duplicate_deleted = self.upload()
        self.assertEqual(duplicate_deleted.json()['items'],[])
        self.assertIn('Trash',duplicate_deleted.json()['errors'][0]['message'])
        self.assertEqual(self.client.post(f'/api/models/{mid}/restore',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get(item['download_url']).content,binary_stl())
        self.assertEqual(len(list((self.data/'files').iterdir())),1)

    def test_concurrent_duplicate_uploads_return_one_shared_model(self):
        self.session()
        ready=threading.Barrier(2)
        def inspect_together(path):
            # Encourage the race when parsing is concurrent, but also permit
            # a correct implementation that serializes model insertion.
            try:
                ready.wait(timeout=.5)
            except threading.BrokenBarrierError:
                pass
            return original_inspect_stl(path)
        with patch('app.main.inspect_stl',side_effect=inspect_together),ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(self.upload,binary_stl(),'one.stl')
            b=pool.submit(self.upload,binary_stl(),'two.stl')
            first,second=a.result(timeout=10),b.result(timeout=10)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(first.json()['errors'],[])
        self.assertEqual(second.json()['errors'],[])
        self.assertEqual(first.json()['items'][0]['id'],second.json()['items'][0]['id'])
        self.assertEqual(len(list((self.data/'files').iterdir())),1)
        self.assertEqual(list((self.data/'incoming').iterdir()),[])

    def test_model_3mf_transforms_build_placements_and_centimeter_units(self):
        self.session()
        item = self.model(model_3mf('centimeter'),'assembly.3mf')
        self.assertEqual(item['dimensions'],{'x':240.0,'y':30.0,'z':40.0})
        self.assertEqual(item['triangle_count'],8)
        self.assertTrue(item['fit_p2s'])
        inches = self.model(model_3mf('inch'),'assembly_inches.3mf')
        self.assertEqual(inches['dimensions'],{'x':609.6,'y':76.2,'z':101.6})
        self.assertFalse(inches['fit_p2s'])

    def test_3mf_zip_paths_duplicates_expansion_and_unsafe_xml_rejected(self):
        self.session()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            duplicate = model_3mf(extra_entries=[('3D/3dmodel.model','<model/>')])
        evil_xml = b'<?xml version="1.0"?><!DOCTYPE model [<!ENTITY x SYSTEM "file:///etc/passwd">]><model>&x;</model>'
        cases = [model_3mf(extra_entries=[('../outside','bad')]), duplicate,
                 model_3mf(extra_entries=[('large.txt',b'0'*(3*1024**2))]),
                 archive_bytes([('3D/3dmodel.model',evil_xml)])]
        for content in cases:
            response = self.upload(content,'unsafe.3mf')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['items'],[])
            self.assertTrue(response.json()['errors'])
        self.assertFalse((self.data.parent/'outside').exists())
        self.assertEqual(list((self.data/'incoming').iterdir()),[])

    def test_cyclic_component_reference_is_rejected(self):
        self.session()
        xml = '<model><resources><object id="1"><components><component objectid="1"/></components></object></resources><build><item objectid="1"/></build></model>'
        response=self.upload(archive_bytes([('3D/3dmodel.model',xml)]),'cycle.3mf')
        self.assertEqual(response.json()['items'],[])
        self.assertIn('cyclic',response.json()['errors'][0]['message'])

    def test_model_edit_persists_and_tag_duplicates_are_removed(self):
        self.session()
        item=self.model()
        response=self.client.put(f"/api/models/{item['id']}",headers=self.headers,
                                 json={'title':'My pot','collection':'Plants','tags':['white','white',' indoor '],'notes':'Print upright.'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['tags'],['white','indoor'])
        self.assertEqual(self.client.get(f"/api/models/{item['id']}").json()['notes'],'Print upright.')

    def test_upgrade_preserves_models_jobs_printer_settings_and_unused_password_hash(self):
        self.session()
        item = self.model()
        job = self.job(item['id'])
        self.configure_printer()
        self.verify_printer()
        printer_before = (self.data / 'printer.json').read_bytes()
        legacy_hash = 'pbkdf2_sha256$600000$existing-salt$existing-password-hash'
        with self.app.state.store.db() as db:
            db.execute("INSERT INTO settings VALUES('password_hash',?)", (legacy_hash,))
            db.execute("INSERT INTO settings VALUES('custom_existing_setting','keep-me')")
        self.client.__exit__(None, None, None)
        self.app = create_app(data_dir=self.data, seed_dir='', initial_password='ignored')
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.assertNotIn(COOKIE, self.client.cookies)
        self.assertEqual(self.client.get('/api/library').json()['items'][0]['id'], item['id'])
        self.assertEqual(self.client.get(item['download_url']).content, binary_stl())
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/file").content, sliced_job())
        self.assertEqual((self.data / 'printer.json').read_bytes(), printer_before)
        self.assertTrue(self.client.get('/api/printer').json()['verified'])
        self.assertEqual(self.app.state.store.setting('password_hash'), legacy_hash)
        self.assertEqual(self.app.state.store.setting('custom_existing_setting'), 'keep-me')
        self.session()
        self.assertEqual(self.client.put(f"/api/models/{item['id']}", headers=self.headers,
                         json={'title': 'Edited after upgrade'}).status_code, 200)

    def test_thumbnail_validates_type_size_and_csrf(self):
        self.session()
        item=self.model()
        url=f"/api/models/{item['id']}/thumbnail"
        self.assertEqual(self.client.get(url).status_code,200)
        image=io.BytesIO()
        capture=Image.new('RGBA',(1000,1000),(40,80,120,255))
        capture.paste((160,120,80,255),(100,100,800,800))
        capture.save(image,'PNG')
        self.assertEqual(self.client.post(url,content=image.getvalue()).status_code,403)
        response=self.client.post(url,headers=self.headers,content=image.getvalue())
        self.assertEqual(response.status_code,200,response.text)
        output=self.client.get(url)
        self.assertEqual(output.headers['content-type'],'image/png')
        with Image.open(io.BytesIO(output.content)) as result:
            self.assertLessEqual(result.width,800)
            self.assertLessEqual(result.height,600)
            self.assertEqual(result.mode,'RGB')
        self.assertEqual(self.client.post(url,headers=self.headers,content=b'not PNG').status_code,400)
        self.assertEqual(self.client.post(url,headers=self.headers,content=b'x'*(8*1024**2+1)).status_code,413)

    def test_jobs_are_distinct_from_models_and_compatibility_is_enforced(self):
        self.session()
        item=self.model()
        model_response=self.upload(sliced_job(),'sliced.gcode.3mf')
        self.assertTrue(model_response.json()['errors'])
        job=self.job(item['id'])
        self.assertTrue(job['compatible'])
        self.assertEqual(job['nozzle_diameter'],'0.4')
        wrong=self.job(item['id'],sliced_job('Bambu Lab P1S'))
        self.assertFalse(wrong['compatible'])
        with patch('app.printer.upload_file') as upload:
            response=self.client.post(f"/api/jobs/{wrong['id']}/send",headers=self.headers,json={})
            self.assertEqual(response.status_code,400)
            upload.assert_not_called()
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/file").content,sliced_job())

    def test_malformed_job_settings_are_a_validation_error_not_server_error(self):
        self.session()
        item=self.model()
        response=self.client.post(f"/api/models/{item['id']}/jobs",headers=self.headers,
                                  files={'file':('bad.gcode.3mf',sliced_job(settings=['invalid'],header_printer=False),'application/octet-stream')})
        self.assertIn(response.status_code,(200,400),response.text)
        if response.status_code==200:
            self.assertFalse(response.json()['compatible'])

    def test_corrupt_sliced_zip_crc_is_a_clean_validation_error(self):
        self.session()
        item=self.model()
        raw=archive_bytes([
            ('Metadata/project_settings.config',json.dumps({'printer_model':'P2S'})),
            ('Metadata/plate_1.gcode','; test sliced file header 000000000000\nG1 X10 Y20\n'),
        ],compression=zipfile.ZIP_STORED)
        corrupted=raw.replace(b'P2S',b'P1S',1)
        response=self.client.post(f"/api/models/{item['id']}/jobs",headers=self.headers,
                                  files={'file':('corrupt.gcode.3mf',corrupted,'application/octet-stream')})
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(self.client.get(f"/api/models/{item['id']}/jobs").json()['items'],[])
        self.assertEqual(list((self.data/'incoming').iterdir()),[])

    def test_start_print_is_always_refused_before_transport(self):
        self.session()
        item=self.model()
        job=self.job(item['id'])
        with patch('app.printer.upload_file') as upload:
            response=self.client.post(f"/api/jobs/{job['id']}/send",headers=self.headers,json={'start_print':True})
        self.assertEqual(response.status_code,400)
        self.assertIn('storage only',response.json()['detail'])
        upload.assert_not_called()

    def test_transfer_requires_saved_successful_connection_test(self):
        self.session()
        item=self.model()
        job=self.job(item['id'])
        self.configure_printer()
        with patch('app.printer.upload_file') as upload:
            response=self.client.post(f"/api/jobs/{job['id']}/send",headers=self.headers,json={})
        self.assertEqual(response.status_code,400)
        upload.assert_not_called()
        self.assertFalse(self.client.get('/api/printer').json()['verified'])

    def test_anonymous_session_mocked_transfer_is_saved_without_starting_print(self):
        self.session()
        item=self.model()
        job=self.job(item['id'])
        self.configure_printer()
        self.verify_printer()
        self.assertTrue(self.client.get('/api/printer').json()['verified'])
        def fake_upload(host,code,path,name,fingerprint,progress):
            self.assertEqual((host,code,fingerprint),('192.168.10.42','testLANaccess',PIN))
            self.assertEqual(Path(path).read_bytes(),sliced_job())
            progress(1,2)
            progress(2,2)
            return {'remote_name':'pot_uploaded.gcode.3mf'}
        with patch('app.printer.upload_file',side_effect=fake_upload) as upload:
            response=self.client.post(f"/api/jobs/{job['id']}/send",headers=self.headers,json={'start_print':False})
            self.assertEqual(response.status_code,200,response.text)
            status=self.wait_transfer(response.json()['transfer_id'])
        upload.assert_called_once()
        self.assertEqual(status['status'],'complete')
        self.assertEqual(status['progress'],100)
        self.assertIn('Start the job from the printer screen',status['message'])
        self.assertNotIn('testLANaccess',json.dumps(status))

    def test_active_transfer_blocks_duplicate_send_and_trash(self):
        self.session()
        item=self.model()
        job=self.job(item['id'])
        self.configure_printer()
        self.verify_printer()
        entered,release=threading.Event(),threading.Event()
        def fake_upload(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test did not release transport')
            return {'remote_name':'pot.gcode.3mf'}
        with patch('app.printer.upload_file',side_effect=fake_upload):
            response=self.client.post(f"/api/jobs/{job['id']}/send",headers=self.headers,json={})
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual(self.client.post(f"/api/jobs/{job['id']}/send",headers=self.headers,json={}).status_code,409)
                self.assertEqual(self.client.delete(f"/api/jobs/{job['id']}",headers=self.headers).status_code,409)
                self.assertEqual(self.client.delete(f"/api/models/{item['id']}",headers=self.headers).status_code,409)
            finally:
                release.set()
            self.assertEqual(self.wait_transfer(response.json()['transfer_id'])['status'],'complete')

    def test_failed_transfer_redacts_access_code(self):
        self.session()
        item=self.model()
        job=self.job(item['id'])
        self.configure_printer()
        self.verify_printer()
        with patch('app.printer.upload_file',side_effect=ValueError('failed with testLANaccess')):
            response=self.client.post(f"/api/jobs/{job['id']}/send",headers=self.headers,json={})
            status=self.wait_transfer(response.json()['transfer_id'])
        self.assertEqual(status['status'],'failed')
        self.assertNotIn('testLANaccess',status['message'])
        self.assertIn('[redacted]',status['message'])

    def test_printer_hosts_and_changed_config_require_reverification(self):
        self.session()
        for host in ['127.0.0.1','8.8.8.8','printer.local','http://192.168.1.2','169.254.1.2']:
            response=self.client.put('/api/printer',headers=self.headers,json={'mode':'lan','host':host,'access_code':'testLANaccess'})
            self.assertEqual(response.status_code,400)
        self.configure_printer()
        self.verify_printer()
        response=self.client.put('/api/printer',headers=self.headers,
                                 json={'mode':'lan','host':'192.168.10.43','access_code':'differentCode'})
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['verified'])
        self.assertNotIn('differentCode',response.text)


if __name__ == '__main__':
    unittest.main()
