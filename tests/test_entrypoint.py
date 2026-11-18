"""The container's real multipart spooling must use data disk, not RAM /tmp."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


@unittest.skipUnless(os.name == 'posix' and Path('/proc/self/fd').is_dir(),
                     'The Docker entrypoint test requires a Linux host.')
class ContainerEntrypointTests(unittest.TestCase):
    def test_multipart_rollover_uses_initialized_data_directory(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / 'data-with-no-existing-subdirectories'
            environment = dict(os.environ, DATA_DIR=str(data), BACKUP_SERVICE_ORIGIN='', SEED_DIR='')
            code = textwrap.dedent('''
                import json, os, struct, tempfile
                from pathlib import Path
                from fastapi.testclient import TestClient
                import starlette.formparsers as parser
                from app.main import create_app

                observed = []
                original = parser.SpooledTemporaryFile
                def track_spooling(*args, **kwargs):
                    temporary = original(*args, **kwargs)
                    rollover = temporary.rollover
                    def record_rollover():
                        rollover()
                        observed.append(os.readlink('/proc/self/fd/' + str(temporary._file.fileno())))
                    temporary.rollover = record_rollover
                    return temporary
                parser.SpooledTemporaryFile = track_spooling

                # 2 MiB: above the multipart parser's in-memory threshold.
                triangles = 42_000
                facet = struct.pack('<12fH', 0, 0, 1, 0, 0, 0, 10, 0, 0, 0, 20, 0, 0)
                payload = b'Independent spool test'.ljust(80, b' ') + struct.pack('<I', triangles) + facet * triangles
                with TestClient(create_app()) as client:
                    token = client.get('/api/session').json()['csrf_token']
                    upload = client.post('/api/models/upload', headers={'X-CSRF-Token': token},
                                         files={'files': ('large-test.stl', payload, 'application/octet-stream')})
                    assert upload.status_code == 200, upload.text
                    result = upload.json()
                    assert result['errors'] == [], result
                    assert result['items'][0]['triangle_count'] == triangles, result
                    assert client.get(result['items'][0]['download_url']).content == payload
                print(json.dumps({'spool_paths': observed, 'tempdir': tempfile.gettempdir()}))
            ''')
            completed = subprocess.run(
                ['sh', str(root / 'docker-entrypoint.sh'), sys.executable, '-c', code],
                cwd=root, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            result = json.loads(completed.stdout)
            expected = str(data / 'incoming')
            self.assertEqual(result['tempdir'], expected)
            self.assertTrue(result['spool_paths'], 'Upload never rolled over to a disk file.')
            for path in result['spool_paths']:
                self.assertTrue(path.startswith(expected + '/'), path)
            self.assertEqual(list((data / 'incoming').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
