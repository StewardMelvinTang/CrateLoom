import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crateloom import __main__ as api
from crateloom import storage


class LibraryLocation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'mac' / '.crateloom'
        self.root.mkdir(parents=True)
        self.drive = self.base / 'drive'
        self.drive.mkdir()
        self.stack = contextlib.ExitStack()
        for name, value in [('ROOT', self.root), ('DB', self.root / 'library.sqlite3'), ('CACHE', self.root / 'analysis-cache'),
                            ('STORAGE_SOURCE', 'default'), ('STORAGE_AVAILABLE', True)]:
            self.stack.enter_context(patch.object(api, name, value))
        self.config = self.base / 'support' / 'storage.json'
        self.stack.enter_context(patch.object(storage, 'CONFIG', self.config))
        self.stack.enter_context(patch.object(storage, 'DEFAULT', self.root))
        storage.MOVE.clear()
        self.addCleanup(storage.MOVE.clear)
        (self.root / 'downloads').mkdir()
        (self.root / 'downloads' / 'song.m4a').write_bytes(b'a' * 5000)
        (self.root / 'artwork').mkdir()
        (self.root / 'artwork' / 'cover.image').write_bytes(b'c' * 300)
        (self.root / 'set-audio' / 'abc').mkdir(parents=True)
        (self.root / 'set-audio' / 'abc' / 'part.m4a').write_bytes(b's' * 2000)
        (self.root / '._junk').write_bytes(b'x')
        self.own = self.base / 'Music' / 'mine.mp3'
        self.own.parent.mkdir()
        self.own.write_bytes(b'm' * 100)
        with api.connect() as db:
            metadata = json.dumps({'values': {'title': 'Song'}, 'pending': {}, 'artwork_path': str(self.root / 'artwork' / 'cover.image')})
            for tid, path in (('downloaded', self.root / 'downloads' / 'song.m4a'), ('own', self.own)):
                db.execute("INSERT INTO tracks(id,path,title,artist,duration,codec,sample_rate,file_hash,metadata) VALUES(?,?,?,?,?,?,?,?,?)",
                           (tid, str(path), 'Song', 'Artist', 10.0, 'aac', 44100, 'hash-' + tid, metadata))
            db.commit()

    def tearDown(self):
        self.stack.close()
        self.tmp.cleanup()

    def call(self, method, **params):
        return storage.dispatch(api, method, params, lambda: None)

    def wait(self):
        deadline = time.monotonic() + 10
        while storage.MOVE.get('state') == 'running' and time.monotonic() < deadline:
            time.sleep(.02)
        return dict(storage.MOVE)

    def test_moving_copies_everything_rewrites_paths_and_keeps_the_old_folder(self):
        plan = self.call('storage.check', path=str(self.drive))
        target = self.drive / 'CrateLoom Library'
        self.assertEqual((plan['mode'], plan['target'], plan['problem']), ('move', str(target), None))
        self.call('storage.move', path=str(self.drive))
        result = self.wait()
        self.assertEqual(result['state'], 'done', result.get('error'))
        self.assertEqual((target / 'downloads' / 'song.m4a').read_bytes(), b'a' * 5000)
        self.assertTrue((target / 'set-audio' / 'abc' / 'part.m4a').is_file())
        self.assertFalse((target / '._junk').exists())
        self.assertEqual([entry.name for entry in self.drive.iterdir()], ['CrateLoom Library'])
        import sqlite3
        db = sqlite3.connect(target / 'library.sqlite3')
        rows = dict(db.execute('SELECT id,path FROM tracks'))
        metadata = json.loads(db.execute("SELECT metadata FROM tracks WHERE id='downloaded'").fetchone()[0])
        db.close()
        self.assertEqual(rows['downloaded'], str(target / 'downloads' / 'song.m4a'))
        self.assertEqual(rows['own'], str(self.own))  # your own files are never moved
        self.assertEqual(metadata['artwork_path'], str(target / 'artwork' / 'cover.image'))
        config = json.loads(self.config.read_text())
        self.assertEqual((config['data'], config['previous']), (str(target), str(self.root)))
        self.assertTrue((self.root / 'downloads' / 'song.m4a').is_file())
        self.assertIn('restart', storage.blocked(api))
        with self.assertRaisesRegex(ValueError, 'restart'):
            api.dispatch('library.list', {})

    def test_the_new_location_is_used_after_restart_and_the_old_copy_can_be_deleted(self):
        self.call('storage.move', path=str(self.drive))
        self.assertEqual(self.wait()['state'], 'done')
        storage.MOVE.clear()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CRATELOOM_DATA', None)
            root, source, available = storage.configured()
        target = self.drive / 'CrateLoom Library'
        self.assertEqual((root, source, available), (target, 'chosen', True))
        with patch.object(api, 'ROOT', target), patch.object(api, 'DB', target / 'library.sqlite3'), patch.object(api, 'STORAGE_SOURCE', 'chosen'):
            status = self.call('storage.status')
            self.assertEqual(status['previous']['path'], str(self.root))
            self.assertTrue(status['usage']['bytes'] >= 7300)
            freed = self.call('storage.discard_previous')
            self.assertFalse(self.root.exists())
            self.assertIsNone(freed['previous'])
            self.assertGreater(freed['freed'], 7000)
            self.assertTrue((target / 'downloads' / 'song.m4a').is_file())

    def test_a_missing_drive_is_not_created_and_requests_explain_it(self):
        gone = self.base / 'Volumes' / 'SHARED SSD 2' / 'CrateLoom Library'
        storage.write_config({'data': str(gone)})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CRATELOOM_DATA', None)
            self.assertEqual(storage.configured(), (gone, 'chosen', False))
        self.assertFalse(gone.parent.exists())
        with patch.object(api, 'ROOT', gone), patch.object(api, 'STORAGE_AVAILABLE', False), patch.object(api, 'STORAGE_SOURCE', 'chosen'):
            self.assertFalse(api.dispatch('handshake', {})['storage_available'])
            with self.assertRaisesRegex(ValueError, 'not connected'):
                api.dispatch('library.list', {})
            self.assertEqual(api.dispatch('system.shutdown', {}), {'stopping': True})
            self.assertFalse(self.call('storage.status')['available'])
            # Starting over on this Mac is allowed while the drive is gone; the old pointer is dropped.
            self.call('storage.use', default=True)
        self.assertNotIn('data', json.loads(self.config.read_text()))
        self.assertFalse(gone.parent.exists())

    def test_folder_choices_are_checked_before_anything_moves(self):
        busy = self.drive / 'busy'
        (busy / 'CrateLoom Library').mkdir(parents=True)
        (busy / 'CrateLoom Library' / 'notes.txt').write_text('mine')
        self.assertIn('not empty', self.call('storage.check', path=str(busy))['problem'])
        self.assertIn('outside the current library', self.call('storage.check', path=str(self.root / 'downloads'))['problem'])
        with patch.object(storage, 'free_space', return_value=1000):
            self.assertIn('not enough free space', self.call('storage.check', path=str(self.drive))['problem'])
            with self.assertRaisesRegex(ValueError, 'not enough free space'):
                self.call('storage.move', path=str(self.drive))
        quoted = self.drive / 'say "hi"'
        quoted.mkdir()
        self.assertIn('quotation marks', self.call('storage.check', path=str(quoted))['problem'])
        with patch.object(api, 'STORAGE_SOURCE', 'environment'), self.assertRaisesRegex(ValueError, 'CRATELOOM_DATA'):
            self.call('storage.check', path=str(self.drive))

    def test_a_folder_with_a_library_is_used_as_it_is(self):
        other = self.drive / 'Old'
        other.mkdir()
        import sqlite3
        db = sqlite3.connect(other / 'library.sqlite3')
        db.execute('CREATE TABLE tracks(id TEXT)')
        db.executemany('INSERT INTO tracks VALUES(?)', [('a',), ('b',)])
        db.commit()
        db.close()
        plan = self.call('storage.check', path=str(other))
        self.assertEqual((plan['mode'], plan['library']['tracks']), ('existing', 2))
        with self.assertRaisesRegex(ValueError, 'already holds'):
            self.call('storage.move', path=str(other))
        self.call('storage.use', path=str(other))
        self.assertEqual(json.loads(self.config.read_text())['data'], str(other))
        self.assertTrue((self.root / 'library.sqlite3').is_file())

    def test_a_stopped_or_failed_move_leaves_the_library_where_it_was(self):
        def stopped(root, staging, cancel):
            (staging / 'partial').write_bytes(b'p')
            raise InterruptedError
        with patch.object(storage, 'copy_library', stopped):
            self.call('storage.move', path=str(self.drive))
            self.assertEqual(self.wait()['state'], 'cancelled')
        storage.MOVE.clear()
        with patch.object(storage, 'verify', side_effect=ValueError('The copy of downloads/song.m4a does not match. Nothing was switched.')):
            self.call('storage.move', path=str(self.drive))
            result = self.wait()
        self.assertEqual(result['state'], 'failed')
        self.assertIn('does not match', result['error'])
        self.assertEqual(list(self.drive.iterdir()), [])
        self.assertFalse(self.config.exists())

    def test_stored_paths_inside_json_text_are_rewritten_exactly(self):
        import sqlite3
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE t(a TEXT, b BLOB)')
        old, sibling = '/Users/me/.crateloom', '/Users/me/.crateloom-other/x.m4a'
        db.executemany('INSERT INTO t VALUES(?,?)', [(old + '/downloads/a.m4a', b'/Users/me/.crateloom/x'), (sibling, None), (old, None),
                                                     (json.dumps({'p': old + '/artwork/c.image'}), None)])
        storage.rewrite_paths(db, [old], '/Volumes/Drive/CrateLoom Library')
        values = [row[0] for row in db.execute('SELECT a FROM t')]
        self.assertEqual(values[:3], ['/Volumes/Drive/CrateLoom Library/downloads/a.m4a', sibling, '/Volumes/Drive/CrateLoom Library'])
        self.assertEqual(json.loads(values[3])['p'], '/Volumes/Drive/CrateLoom Library/artwork/c.image')
        self.assertEqual(db.execute('SELECT b FROM t').fetchone()[0], b'/Users/me/.crateloom/x')


if __name__ == '__main__':
    unittest.main()
