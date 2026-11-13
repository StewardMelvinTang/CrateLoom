import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

from crateloom import __main__ as api
from crateloom import errors, preparation, service_links
from crateloom.library_imports import ImportCancelled


def settle(job, timeout=5):
    deadline = time.monotonic()+timeout
    while preparation.JOBS[job['id']]['state'] == 'running':
        if time.monotonic() > deadline:
            raise AssertionError('job did not finish')
        time.sleep(.01)
    return preparation.JOBS[job['id']]


class EngineErrorTests(unittest.TestCase):
    def test_launch_zlib_error_is_engine_updated_with_detail(self):
        def broken():
            raise zlib.error('Error -3 while decompressing data: incorrect header check')
        job = settle(preparation.launch(broken))
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(job['error'], 'CrateLoom was updated while it was open. Restart CrateLoom to continue.')
        self.assertEqual(job['error_code'], 'engine_updated')
        self.assertEqual(job['error_detail'], 'error: Error -3 while decompressing data: incorrect header check')
        # Plain sentences pass through unchanged and carry no detail.
        job = settle(preparation.launch(lambda: (_ for _ in ()).throw(ValueError('Choose a single public YouTube mix.'))))
        self.assertEqual((job['error'], job['error_code'], job['error_detail']), ('Choose a single public YouTube mix.', 'engine_error', None))

    def test_frozen_import_error_and_replaced_executable(self):
        with patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(errors.public_error(ModuleNotFoundError("No module named 'crateloom.youtube_tracklist'"))[1], 'engine_updated')
        self.assertEqual(errors.public_error(ImportError('optional'))[1], 'internal')
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder)/'crateloom-engine'
            binary.write_bytes(b'old')
            with patch.object(sys, 'executable', str(binary)), patch.object(errors, 'EXECUTABLE_ID', errors.executable_identity(binary)):
                self.assertFalse(errors.executable_replaced())
                replacement = Path(folder)/'new'
                replacement.write_bytes(b'new build')
                replacement.replace(binary)
                self.assertTrue(errors.executable_replaced())
                message, code, detail = errors.public_error(zlib.error('Error -3'))
                self.assertEqual(code, 'engine_updated')
                self.assertTrue(detail.endswith(' (the app file changed)'))

    def test_dispatch_unknown_exception_plain_message(self):
        def reply(error):
            requests = json.dumps({'protocol_version': 1, 'request_id': 'r1', 'method': 'library.list', 'params': {}})+'\n'
            output = io.StringIO()
            with patch.object(api, 'dispatch', side_effect=error), patch('crateloom.set_import.start_auto_scans'), \
                 patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(requests.encode()))), patch.object(sys, 'stdout', output):
                api.main()
            return json.loads(output.getvalue())['error']
        self.assertEqual(reply(RuntimeError('secret internals at /Users/someone')),
                         {'code': 'internal', 'message': 'CrateLoom hit an internal error. Try again.',
                          'detail': 'RuntimeError: secret internals at /Users/someone'})
        self.assertEqual(reply(ValueError('Playlist not found')), {'code': 'engine_error', 'message': 'Playlist not found'})
        self.assertEqual(reply(ImportCancelled('Scan cancelled'))['message'], 'Scan cancelled')
        self.assertEqual(reply(FileNotFoundError(2, 'No such file or directory'))['message'], 'A file CrateLoom needed is missing.')
        self.assertEqual(reply(ConnectionResetError(54, 'Connection reset by peer'))['message'], 'A file or network operation failed. Try again.')
        self.assertEqual(reply(KeyError('method'))['code'], 'internal')

    def test_locked_db_is_busy(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'locked.sqlite3')
            first = sqlite3.connect(path, isolation_level=None)
            first.execute('CREATE TABLE t(x)')
            first.execute('BEGIN IMMEDIATE')
            second = sqlite3.connect(path, timeout=0, isolation_level=None)
            try:
                with self.assertRaises(sqlite3.OperationalError) as caught:
                    second.execute('BEGIN IMMEDIATE')
            finally:
                first.close()
                second.close()
        message, code, detail = errors.public_error(caught.exception)
        self.assertEqual((message, code), ('CrateLoom was busy. Try again.', 'busy'))
        self.assertIn('database is locked', detail)
        self.assertEqual(errors.public_error(sqlite3.OperationalError('no such table: x'))[1], 'internal')

    def test_strip_tracking_params(self):
        strip = service_links.strip_tracking
        self.assertEqual(strip('https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n?si=abc'),
                         'https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n')
        self.assertEqual(strip('https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&pp=ygU&feature=share&t=42s&si=x'),
                         'https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123')
        self.assertEqual(strip('https://soundcloud.com/a/b?utm_source=clipboard&utm_campaign=wtshare&ref=x&fbclid=y&igsh=z&igshid=w'),
                         'https://soundcloud.com/a/b')
        self.assertEqual(strip(' https://youtu.be/dQw4w9WgXcQ?si=abc '), 'https://youtu.be/dQw4w9WgXcQ')
        unchanged = 'https://www.mixcloud.com/a/b/?page=2'
        self.assertEqual(strip(unchanged), unchanged)
        self.assertEqual(strip('https://www.youtube.com/watch?v=dQw4w9WgXcQ'), 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')

    def test_eager_module_list_matches_package(self):
        package = Path(errors.__file__).resolve().parent
        modules = {'crateloom.'+path.stem for path in package.glob('*.py')
                   if not path.name.startswith('._') and path.stem not in ('__main__', '__init__')}
        self.assertTrue(modules)
        self.assertLessEqual(modules, set(errors.EAGER_MODULES))
        self.assertIn('crateloom.set_discovery', errors.EAGER_MODULES)
        self.assertNotIn('crateloom.__main__', errors.EAGER_MODULES)
        # Startup tolerates a module that cannot be imported.
        with patch.object(errors, 'EAGER_MODULES', ('crateloom.service_links', 'crateloom.not_there')), \
             patch.object(errors, 'EAGER_STDLIB', ('zipfile',)), patch.object(errors, 'EXECUTABLE_ID', None):
            errors.preload()
            self.assertEqual(errors.EXECUTABLE_ID, errors.executable_identity())


if __name__ == '__main__':
    unittest.main()
